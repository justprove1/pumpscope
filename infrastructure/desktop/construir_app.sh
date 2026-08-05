#!/bin/bash
# Construye «Pumpscope Trader.app»: el panel de operativa como programa de escritorio.
#
# Por que un navegador por dentro y no Electron o Tauri: la cartera. Phantom es una
# EXTENSION de navegador, y en Electron o Tauri sencillamente no existe —no soportan
# extensiones Manifest V3—. Un programa nativo tendria que custodiar la clave privada, que es
# justo lo que el proyecto no hace: aqui cada orden la firma el usuario en una ventana que el
# programa no controla. Asi que la ventana ES un navegador en modo aplicacion: sin barra de
# direcciones, sin pestañas, con su propio icono en el Dock. Se ve y se usa como un programa,
# y la cartera sigue funcionando.
#
# Uso:  bash infrastructure/desktop/construir_app.sh [destino]
#       (por defecto se crea en la raiz del repositorio)

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Se instala en ~/Applications, no en la carpeta del proyecto: ahi es donde macOS busca los
# programas, asi que sale en Launchpad y en Spotlight como cualquier otro. En /Applications
# no, porque esa necesita contraseña de administrador y esto es de un solo usuario.
DESTINO="${1:-$HOME/Applications}"
mkdir -p "$DESTINO"
APP="$DESTINO/Pumpscope Trader.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# El icono se reutiliza del bundle que ya existe, si esta.
if [ -f "$RAIZ/Pumpscope.app/Contents/Resources/appicon.icns" ]; then
  cp "$RAIZ/Pumpscope.app/Contents/Resources/appicon.icns" "$APP/Contents/Resources/appicon.icns"
fi

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Pumpscope Trader</string>
  <key>CFBundleDisplayName</key><string>Pumpscope Trader</string>
  <key>CFBundleIdentifier</key><string>local.pumpscope.trader</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>trader</string>
  <key>CFBundleIconFile</key><string>appicon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <!-- Sin interfaz propia: la ventana la aporta el motor web. -->
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

# La ruta del repositorio se incrusta al construir: el programa necesita el docker-compose.
cat > "$APP/Contents/MacOS/trader" <<LANZADOR
#!/bin/bash
# Lanzador de Pumpscope Trader. Generado por infrastructure/desktop/construir_app.sh
REPO="$RAIZ"
LANZADOR

cat >> "$APP/Contents/MacOS/trader" <<'LANZADOR'

set -u

PERFIL="$HOME/Library/Application Support/Pumpscope Trader/motor"
LOG="$HOME/Library/Logs/Pumpscope Trader.log"
# Id de Phantom en la tienda de Chrome. Sin la extension en ESTE perfil no hay cartera.
PHANTOM_ID="bfnaelmomeimhlpmgjnjophhpkkoljpa"
TIENDA="https://chromewebstore.google.com/detail/phantom/$PHANTOM_ID"
PANEL="http://localhost:4000"

mkdir -p "$(dirname "$LOG")" "$PERFIL"
exec 2>>"$LOG"
echo "--- arranque $(date)" >>"$LOG"

aviso() { /usr/bin/osascript -e "display alert \"Pumpscope Trader\" message \"$1\" as critical" >/dev/null 2>&1; }
# `giving up after` es obligatorio: sin el, un dialogo abierto sin nadie delante deja el
# programa colgado para siempre en vez de abrir la ventana.
pregunta() { /usr/bin/osascript -e "display dialog \"$1\" buttons {\"Cancelar\",\"$2\"} default button \"$2\" with title \"Pumpscope Trader\" giving up after 120" >/dev/null 2>&1; }

# --- Docker -----------------------------------------------------------------
# Una app de GUI arranca con un PATH minimo: hay que buscar el binario a mano.
DOCKER=""
for d in /usr/local/bin/docker /opt/homebrew/bin/docker \
         /Applications/Docker.app/Contents/Resources/bin/docker; do
  [ -x "$d" ] && DOCKER="$d" && break
done
[ -n "$DOCKER" ] || { aviso "No encuentro Docker. Instala Docker Desktop y vuelve a abrir."; exit 1; }

if ! "$DOCKER" info >/dev/null 2>&1; then
  open -ga Docker 2>/dev/null
  for _ in $(seq 1 60); do
    "$DOCKER" info >/dev/null 2>&1 && break
    sleep 1
  done
  "$DOCKER" info >/dev/null 2>&1 || { aviso "Docker no llegó a arrancar. Ábrelo a mano y reintenta."; exit 1; }
fi

# Solo lo que el panel necesita: nada de dashboards ni observabilidad. El worker no hace
# falta —el panel ya no usa la lista del radar— y no arrancarlo abrevia el arranque.
cd "$REPO" || { aviso "No encuentro el proyecto en $REPO"; exit 1; }
SERVICIOS="postgres redis api panel"

# Que habia ANTES de abrir el programa. Al cerrar se dejara el sistema como estaba: parar
# algo que el usuario tenia corriendo por su cuenta seria meterse donde no llaman, y dejar
# encendido lo que encendio el programa seria no recoger la mesa.
YA_ESTABAN="$("$DOCKER" compose ps --services --status running 2>/dev/null | tr '\n' ' ')"

"$DOCKER" compose up -d $SERVICIOS >>"$LOG" 2>&1 || {
  aviso "No se pudieron arrancar los servicios. Mira $LOG"; exit 1; }

recoger() {
  local sobra=""
  for s in $SERVICIOS; do
    case " $YA_ESTABAN " in
      *" $s "*) ;;                 # ya estaba antes: no se toca
      *) sobra="$sobra $s" ;;
    esac
  done
  [ -n "$sobra" ] && "$DOCKER" compose stop $sobra >>"$LOG" 2>&1
}
trap recoger EXIT INT TERM

# --- Esperar a que el panel responda ---------------------------------------
for _ in $(seq 1 90); do
  /usr/bin/curl -s -o /dev/null --max-time 1 "$PANEL" && break
  sleep 1
done
/usr/bin/curl -s -o /dev/null --max-time 2 "$PANEL" || {
  aviso "El panel no respondió a tiempo. Mira $LOG"; exit 1; }

# --- Motor de ventana -------------------------------------------------------
MOTOR="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
NAVEGADOR="Brave"
if [ ! -x "$MOTOR" ]; then
  MOTOR="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  NAVEGADOR="Chrome"
fi
[ -x "$MOTOR" ] || { aviso "Necesito Brave o Chrome instalado: la ventana del programa y la cartera Phantom van por ahí."; exit 1; }

# **Se usa TU navegador de siempre, con tu perfil.** Es donde ya vive Phantom, asi que no hay
# que instalar nada ni volver a meter ninguna frase de recuperacion. La ventana sigue siendo
# de aplicacion —sin barra de direcciones ni pestañas—, solo que comparte sesion con tu
# navegacion normal.
#
# Si algun dia se prefiere lo contrario —un perfil aislado, con su identidad propia en el
# Dock y su Phantom aparte— basta con exportar PUMPSCOPE_PERFIL_PROPIO=1 antes de abrir.
PERFIL_PROPIO="${PUMPSCOPE_PERFIL_PROPIO:-0}"
PERFIL="$HOME/Library/Application Support/Pumpscope Trader/motor"
PHANTOM_ID="bfnaelmomeimhlpmgjnjophhpkkoljpa"
TIENDA="https://chromewebstore.google.com/detail/phantom/$PHANTOM_ID"

if [ "$PERFIL_PROPIO" = "1" ]; then
  mkdir -p "$PERFIL"
  DONDE_PHANTOM="$PERFIL/Default/Extensions/$PHANTOM_ID"
else
  BASE_BRAVE="$HOME/Library/Application Support/BraveSoftware/Brave-Browser/Default/Extensions"
  BASE_CHROME="$HOME/Library/Application Support/Google/Chrome/Default/Extensions"
  DONDE_PHANTOM="$BASE_BRAVE/$PHANTOM_ID"
  [ -d "$DONDE_PHANTOM" ] || DONDE_PHANTOM="$BASE_CHROME/$PHANTOM_ID"
fi

# Solo se avisa; no se bloquea. Puede haber Phantom en otro perfil del navegador, y el panel
# ya dice con claridad que cartera responde cuando se pulsa conectar.
if [ ! -d "$DONDE_PHANTOM" ]; then
  if pregunta "No veo Phantom en el perfil de $NAVEGADOR que va a usar el programa.\n\n¿Abro su página de instalación? Es solo la primera vez." "Instalar Phantom"; then
    if [ "$PERFIL_PROPIO" = "1" ]; then
      "$MOTOR" --user-data-dir="$PERFIL" "$TIENDA" >/dev/null 2>&1 &
    else
      "$MOTOR" "$TIENDA" >/dev/null 2>&1 &
    fi
    sleep 4
  fi
fi

# --- Donde aparece el panel -------------------------------------------------
# **Ventana propia de programa.** Sin barra de direcciones, sin pestañas, sin marcadores: no
# se ve ningun localhost por ninguna parte. Se mueve, se redimensiona y se cierra como
# cualquier aplicacion, y macOS le guarda el tamaño y la posicion entre aperturas.
#
# Sigue siendo el motor de Brave por dentro, y eso es a proposito, no una limitacion que se
# haya dejado sin resolver: Phantom es una EXTENSION de navegador. Fuera de un navegador no
# existe, y sin ella no hay firma. La alternativa —un binario nativo— obligaria a que el
# programa custodiara la clave privada, que es exactamente lo que este proyecto no hace.
#
# Con PUMPSCOPE_PESTANA=1 se vuelve al comportamiento anterior: una pestaña mas dentro de la
# ventana de Brave donde ya esta pump.fun.
VENTANA_PROPIA="${PUMPSCOPE_VENTANA_PROPIA:-1}"
[ "${PUMPSCOPE_PESTANA:-0}" = "1" ] && VENTANA_PROPIA=0

# Nombre del navegador tal y como lo conoce AppleScript, que no es el mismo que usa `open`.
APP_NAV="Brave Browser"
[ "$NAVEGADOR" = "Chrome" ] && APP_NAV="Google Chrome"

# Si el panel YA esta abierto —da igual si en ventana propia o en pestaña— se trae al frente
# en vez de abrir otro: dos copias del mismo panel son dos sitios donde mirar el mismo stop,
# y solo hace falta que una de las dos se quede atras para operar con un precio viejo.
#
# Se comprueba antes si el navegador corre. Sin esa guarda, el propio `tell application`
# lo ARRANCA para poder preguntarle, y entonces la respuesta siempre seria «no habia nada
# abierto» despues de haber abierto medio navegador para averiguarlo.
ya_abierto() {
  /usr/bin/pgrep -qf "$APP_NAV" || return 1
  [ "$(/usr/bin/osascript <<APPLE 2>/dev/null
tell application "$APP_NAV"
  repeat with w in windows
    repeat with t in tabs of w
      if URL of t contains "localhost:4000" then
        set index of w to 1
        activate
        return "si"
      end if
    end repeat
  end repeat
end tell
return "no"
APPLE
)" = "si" ]
}

abrir_en_pestana() {
  /usr/bin/osascript <<'APPLE' 2>/dev/null
tell application "Brave Browser"
  set encontrada to false
  repeat with w in windows
    set i to 0
    repeat with t in tabs of w
      set i to i + 1
      if URL of t contains "localhost:4000" then
        set active tab index of w to i
        set index of w to 1
        set encontrada to true
        exit repeat
      end if
    end repeat
    if encontrada then exit repeat
  end repeat
  if not encontrada then
    if (count of windows) is 0 then
      make new window
      set URL of active tab of front window to "http://localhost:4000"
    else
      tell front window to make new tab with properties {URL:"http://localhost:4000"}
    end if
  end if
  activate
end tell
APPLE
}

if ya_abierto; then
  echo "el panel ya estaba abierto: traido al frente" >>"$LOG"
elif [ "$VENTANA_PROPIA" = "1" ]; then
  # `--app=` es lo que convierte la ventana en ventana de programa: quita barra de
  # direcciones, pestañas, marcadores y menu. Queda el panel y nada mas.
  #
  # El tamaño se da en `--window-size` y no en `--start-maximized` porque este ultimo solo
  # lo obedece el navegador al arrancar de cero; si Brave ya estaba abierto —que es lo
  # normal, con pump.fun delante— se ignora sin decir nada y la ventana sale minuscula.
  VENTANA="${PUMPSCOPE_TAMANO:-1280,900}"
  if [ "$PERFIL_PROPIO" = "1" ]; then
    "$MOTOR" --app="$PANEL" --user-data-dir="$PERFIL" --window-size="$VENTANA" \
      --no-first-run --no-default-browser-check >/dev/null 2>&1 &
  else
    "$MOTOR" --app="$PANEL" --window-size="$VENTANA" \
      --no-first-run --no-default-browser-check >/dev/null 2>&1 &
  fi
  sleep 5
else
  # Se intenta por AppleScript, que es lo unico que sabe reutilizar una pestaña existente.
  # macOS pedira permiso de automatizacion la primera vez; si se deniega, se abre igual con
  # `open`, que crea pestaña nueva en la ventana de delante.
  abrir_en_pestana || /usr/bin/open -a "$NAVEGADOR Browser" "$PANEL" 2>/dev/null \
                   || /usr/bin/open "$PANEL"
fi

# El panel vive dentro de la sesion del usuario: cerrar su pestaña no deja ninguna señal que
# se pueda leer sin fisgar en el resto. No se apaga nada solo; se para con `docker compose stop`.
echo "panel abierto en la sesión de $NAVEGADOR; los servicios se quedan en marcha" >>"$LOG"
trap - EXIT INT TERM
LANZADOR

chmod +x "$APP/Contents/MacOS/trader"

# Refresca el icono en el Finder/Dock.
touch "$APP"

echo "construido: $APP"
echo
echo "Ábrelo con doble clic, o:  open '$APP'"
