// Pumpscope Trader — programa de escritorio.
//
// Una ventana nativa de verdad: proceso propio, icono propio en el Dock, nombre propio en
// ⌘-Tab. Sin navegador, sin Docker y sin ningun puerto a la vista.
//
// Por dentro arranca la API en 127.0.0.1 y le pide el panel. Eso sigue siendo una conexion
// local, porque el motor tiene que hablar con la cadena desde algun sitio, pero es interna al
// programa: el puerto se elige libre en cada arranque, solo escucha en la maquina, y no
// aparece en ninguna parte de la interfaz.
//
// LO QUE ESTA VENTANA NO PUEDE HACER, y conviene tenerlo claro: firmar con Phantom. Phantom
// es una extension de navegador Manifest V3 y aqui no existe. Se comprobo cargandola en
// Electron 43: la extension se carga pero NO inyecta `window.phantom` ni `window.solana`. Por
// eso el programa necesita una fuente de firma propia; mientras no la tenga configurada, el
// panel funciona entero salvo el momento de firmar.

const {app, BrowserWindow, shell, dialog} = require('electron')
const {spawn} = require('child_process')
const net = require('net')
const path = require('path')
const fs = require('fs')

const RAIZ = process.env.PUMPSCOPE_RAIZ || path.resolve(__dirname, '..', '..')
let backend = null
let firmante = null
let ventana = null

/** Un puerto que ahora mismo esta libre. Se pide al sistema en vez de fijar uno: un numero
 *  fijo choca con lo que el usuario ya tenga levantado, y el fallo aparece como una ventana
 *  en blanco sin explicacion. */
function puertoLibre () {
  return new Promise((resolve, reject) => {
    const s = net.createServer()
    s.once('error', reject)
    s.listen(0, '127.0.0.1', () => {
      const p = s.address().port
      s.close(() => resolve(p))
    })
  })
}

/** El interprete de Python que lleva el programa. En desarrollo, el del repositorio. */
function interprete () {
  const candidatos = [
    process.env.PUMPSCOPE_PYTHON,
    path.join(RAIZ, '.venv', 'bin', 'python'),
  ].filter(Boolean)
  for (const c of candidatos) if (fs.existsSync(c)) return c
  return null
}

async function esperarApi (puerto, segundos = 60) {
  const hasta = Date.now() + segundos * 1000
  while (Date.now() < hasta) {
    try {
      const r = await fetch(`http://127.0.0.1:${puerto}/health`)
      if (r.ok) return true
    } catch { /* aun no escucha */ }
    await new Promise(r => setTimeout(r, 300))
  }
  return false
}

function morir (mensaje, detalle) {
  dialog.showErrorBox(mensaje, detalle || '')
  app.quit()
}

async function arrancar () {
  const python = interprete()
  if (!python) {
    return morir('Falta el motor',
      'No encuentro el interprete de Python del programa.\n\n' +
      'En desarrollo se espera en .venv/bin/python dentro del proyecto, o en la ' +
      'variable PUMPSCOPE_PYTHON.')
  }

  const PYTHONPATH = ['apps/api', 'apps/signer', 'packages/pumpfun', 'packages/solana',
    'packages/shared'].map(p => path.join(RAIZ, p)).join(':')

  // Los datos del firmante van a la carpeta del programa, no al repositorio: es donde macOS
  // guarda lo de cada aplicacion y donde no se borra al hacer limpieza del proyecto.
  const datos = app.getPath('userData')
  fs.mkdirSync(datos, {recursive: true})

  // --- Firmante -------------------------------------------------------------
  // Proceso APARTE, no un modulo de la API. Es lo que hace que un fallo en la API no pueda
  // tocar la clave: para gastar hay que pedirselo a este, y este valida por su cuenta.
  //
  // Arranca encendido porque su cartera nace VACIA. Encender un firmante sin fondos no
  // arriesga nada, y evita que la primera compra falle por una casilla sin marcar.
  const puertoFirmante = await puertoLibre()
  firmante = spawn(python, [
    '-m', 'uvicorn', 'mit_signer.servicio:app',
    '--host', '127.0.0.1', '--port', String(puertoFirmante), '--log-level', 'warning',
  ], {
    cwd: RAIZ,
    env: {
      ...process.env,
      PYTHONPATH,
      SIGNER_MODE: 'local_encrypted',
      SIGNER_KEY_PATH: path.join(datos, 'trading_key.enc'),
      SIGNER_PASSWORD_PATH: path.join(datos, 'key_password'),
      SIGNER_COUNTER_PATH: path.join(datos, 'gastado_hoy.json'),
      SIGNER_MAX_ORDER_SOL: process.env.SIGNER_MAX_ORDER_SOL || '0.05',
      SIGNER_MAX_DAILY_SOL: process.env.SIGNER_MAX_DAILY_SOL || '0.2',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  const puerto = await puertoLibre()
  backend = spawn(python, [
    '-m', 'uvicorn', 'mit_api.escritorio:app',
    '--host', '127.0.0.1', '--port', String(puerto), '--log-level', 'warning',
  ], {
    cwd: RAIZ,
    env: {
      ...process.env,
      PYTHONPATH,
      PUMPSCOPE_PANEL: path.join(RAIZ, 'apps', 'panel'),
      // La API sabe DONDE esta el firmante, pero no tiene la clave ni puede sacarsela.
      SIGNER_URL: `http://127.0.0.1:${puertoFirmante}`,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  // Lo que escupa el motor se guarda: si el programa no abre, ahi esta el motivo. Sin esto
  // un fallo de arranque es una ventana en blanco y nada mas.
  const registro = path.join(app.getPath('logs'), 'motor.log')
  fs.mkdirSync(path.dirname(registro), {recursive: true})
  const salida = fs.createWriteStream(registro, {flags: 'a'})
  backend.stdout.pipe(salida)
  backend.stderr.pipe(salida)
  firmante.stdout.pipe(salida)
  firmante.stderr.pipe(salida)

  let ultimoError = ''
  backend.stderr.on('data', d => { ultimoError = String(d).slice(-800) })
  backend.on('exit', code => {
    backend = null
    if (code !== 0 && !app.isQuittingPumpscope) {
      morir('El motor se ha parado', `Codigo ${code}.\n\n${ultimoError}\n\nRegistro: ${registro}`)
    }
  })

  if (!await esperarApi(puerto)) {
    return morir('El motor no arranco a tiempo', `${ultimoError}\n\nRegistro: ${registro}`)
  }

  // Tamano y posicion de la ultima vez. Sin esto, encoger la ventana no sirve de nada: se
  // vuelve a abrir grande cada vez y hay que recolocarla a mano en cada arranque.
  const marco = path.join(datos, 'ventana.json')
  let guardado = {}
  try { guardado = JSON.parse(fs.readFileSync(marco, 'utf8')) } catch { /* primera vez */ }

  ventana = new BrowserWindow({
    width: guardado.width || 1180, height: guardado.height || 900,
    // La posicion solo se aplica si venia guardada; si no, macOS la centra el, que es mejor
    // que un (0,0) nuestro. Si la pantalla de entonces ya no existe, Electron la recoloca.
    ...(Number.isInteger(guardado.x) && Number.isInteger(guardado.y)
        ? {x: guardado.x, y: guardado.y} : {}),
    // Minimos pequenos a proposito: el uso real de esta ventana es quedarse en una esquina
    // vigilando el stop mientras se hace otra cosa, y con 900x700 no cabia en ninguna esquina.
    // El panel se reordena por debajo de 720, 560 y 430 px para que a este tamano siga siendo
    // legible en vez de quedar una rejilla de columnas espachurradas.
    minWidth: 340, minHeight: 380,
    title: 'Pumpscope Trader',
    backgroundColor: '#0b0e14',
    // La barra de titulo se funde con el panel, que es oscuro: una franja gris encima de una
    // interfaz negra delata que dentro hay una pagina web.
    titleBarStyle: 'hiddenInset',
    show: false,
    webPreferences: {nodeIntegration: false, contextIsolation: true},
  })

  ventana.once('ready-to-show', () => {
    ventana.show()
    const b = ventana.getBounds()
    // Se deja constancia de que la ventana llego a pintarse. Sin esto, «no se abre» y «se
    // abrio fuera de la pantalla» son el mismo sintoma y no hay forma de distinguirlos.
    console.log(`[ventana] visible ${b.width}x${b.height} en (${b.x},${b.y})`)
  })
  ventana.webContents.on('did-fail-load', (e, code, desc, url) => {
    console.log(`[ventana] fallo al cargar ${url}: ${desc} (${code})`)
  })
  ventana.loadURL(`http://127.0.0.1:${puerto}/`)

  // Cualquier enlace externo —pump.fun, un explorador de bloques— se abre en el navegador de
  // siempre. Dentro del programa solo vive el panel; convertir esta ventana en un navegador
  // improvisado es como se acaba navegando por sitios que no pintan nada aqui.
  ventana.webContents.setWindowOpenHandler(({url}) => {
    if (/^https?:/.test(url)) shell.openExternal(url)
    return {action: 'deny'}
  })
  ventana.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${puerto}`)) {
      e.preventDefault()
      shell.openExternal(url)
    }
  })

  // Se anota al mover y al redimensionar, no solo al cerrar: si el programa se va abajo de
  // golpe, lo ultimo que hizo el usuario ya esta guardado. Con retardo para no escribir en
  // disco en cada pixel de un arrastre.
  let pendiente = null
  const recordar = () => {
    if (!ventana || ventana.isMinimized() || ventana.isFullScreen()) return
    clearTimeout(pendiente)
    pendiente = setTimeout(() => {
      try { fs.writeFileSync(marco, JSON.stringify(ventana.getBounds())) } catch { /* da igual */ }
    }, 400)
  }
  ventana.on('resize', recordar)
  ventana.on('move', recordar)

  ventana.on('closed', () => { ventana = null })
}

app.whenReady().then(arrancar)

app.on('window-all-closed', () => app.quit())

// Al cerrar se para el motor. Dejarlo suelto significa un proceso escuchando y consultando la
// cadena para nadie, y al siguiente arranque otro mas.
app.on('before-quit', () => {
  app.isQuittingPumpscope = true
  if (backend) { backend.kill('SIGTERM'); backend = null }
  // El firmante se para SIEMPRE al cerrar. Dejarlo suelto seria dejar un proceso con la
  // clave descifrada en memoria y un puerto abierto despues de cerrar el programa.
  if (firmante) { firmante.kill('SIGTERM'); firmante = null }
})
