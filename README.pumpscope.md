# pumpscope

Analiza un token de pump.fun y devuelve **3 escenarios con probabilidad y nivel de precio**, calculados sobre datos reales de la cadena. Sin API keys, sin dependencias.

```bash
python3 pumpscope.py https://pump.fun/coin/<mint>
```

| Opción | Qué hace |
|---|---|
| `--why` | Desglosa cómo se construyó cada probabilidad, señal por señal |
| `--horizonte H` | Horizonte en horas (por defecto 6) |
| `--live` | Analiza una vez y deja el precio corriendo en directo |
| `--buscar` | Busca memecoins en posible tendencia alcista (sin pasar mint) |
| `--json` | Salida JSON para conectarlo a otra cosa |
| `--watch N` | Reanaliza cada N segundos |

Acepta links de pump.fun, DexScreener, Solscan o el mint pegado a pelo.

También hay interfaz web local, con precio en vivo y buscador:

```bash
python3 serve.py --puerto 8787
```

---

## De dónde salen los datos

Tres fuentes públicas, todas verificadas en vivo y sin key:

- **`frontend-api-v3.pump.fun`** — reservas de la curva, creator, ATH, flag de graduación.
- **`api.geckoterminal.com`** — velas OHLCV reales y los últimos ~300 trades **con la wallet de cada uno** (esto es lo que permite detectar al dev vendiendo).
- **`api.dexscreener.com`** — liquidez y volumen por ventanas.

## Tres decisiones que cambian el resultado

**1. El umbral de graduación no son $69.000.**
Esa cifra que repite todo el mundo es un artefacto de cuando SOL valía ~$168. El umbral real está fijado **en SOL** y se deriva de la invariante `x·y=k` de la propia curva: unos **410 SOL de market cap**, que a precio de hoy son ~$30k. La herramienta lo recalcula desde las reservas en vivo de cada token, así que sigue siendo correcto aunque pump.fun cambie parámetros o SOL se mueva.

**2. La volatilidad no se escala con `sqrt(t)`.**
Escalar con la raíz del tiempo asume retornos independientes. En un memecoin no lo son: hay ráfagas cortas y violentas seguidas de reversión. El exponente de escalado real se **mide** sobre los retornos del propio token (varianza agregada, tipo Hurst) en vez de asumirlo. Sale típicamente entre 0,32 y 0,57, y se muestra en el informe.

**3. Si no hay datos, el horizonte se recorta.**
Un token de 4 minutos no permite pronosticar a 6 horas: extrapolar la volatilidad por un factor de 10.000 da cifras como "+213.000%", que es basura con formato bonito. El horizonte se limita a 3× el histórico observado y se te dice el que se acabó usando.

**4. Los objetivos no son ±1σ simétricos.**
Un memecoin sube en ráfagas y baja en escalones: asumir una lognormal simétrica borra justo esa asimetría. En su lugar se **remuestrean por bloques los retornos reales del token** (bloques, no sueltos, para conservar la autocorrelación) hasta el horizonte, y los objetivos salen de cuantiles de esa distribución. El objetivo alcista es la *mediana de la cola superior*: «si rompe, hasta dónde suele llegar». La zona de rango son los cuartiles. Todo asimétrico por construcción.

**5. La volatilidad se mide con Garman-Klass, no cierre a cierre.**
El estimador cierre-a-cierre tira el recorrido intravela: dos velas con el mismo cierre cuentan igual aunque una haya oscilado un 40%. Garman-Klass usa el rango alto-bajo y es ~7 veces más eficiente con la misma muestra. En velas con mechas enormes, no es cosmético.

## Veredicto de tendencia

Una etiqueta en cabecera: **alcista fuerte / alcista / neutra / bajista / bajista fuerte**, más un caso propio: **agotándose**.

No sale de una regla suelta. Combina el sesgo del modelo (`P(subida) − P(bajada)`) con la alineación de las ventanas de tiempo, y solo llega a "fuerte" cuando **ambas** empujan igual — que dos medidas independientes coincidan es lo que separa una tendencia de un rebote suelto. El caso "agotándose" se dispara cuando sube en las ventanas largas pero ya se gira en las cortas: así se forma un techo, y etiquetarlo de alcista sería justo el error caro.

Debajo, siempre, la razón con sus cifras.

## Los 3 objetivos en capitalización, en vivo

```
TECHO · 44%        RANGO · 28%           SUELO · 28%
$8.01M             $6.38M — $7.14M       $5.67M
+11.0% desde aquí  -1.0% para volver     -21.5% desde aquí
```

Las tres cifras son niveles fijos del análisis. Lo que se actualiza con cada tick es **tu distancia a cada una** — que es lo accionable: no importa tanto que el techo esté en $8.01M como que ahora mismo esté a +11.0%. El rango dice si estás dentro, o cuánto falta para entrar o volver.

## Explicación en prosa

```bash
python3 pumpscope.py <mint> --explicar subida
```

Siete párrafos que explican de dónde sale la cifra: el base rate del que parte, qué señales la movieron y cuánto, qué juega en contra, si hubo recorte por correlación, de dónde sale el objetivo y con qué límites leerlo. En la web, un botón por escenario.

Se genera de las **mismas contribuciones** que alimentan la tabla de auditoría, así que no puede contradecirlas. Regla de la casa: cada frase lleva un número medido, y si no puede apoyarse en uno, no se escribe.

## Barrera compradores / vendedores

Tres lecturas del mismo pulso, porque cuando divergen la discrepancia dice más que cualquiera por separado:

```
operaciones  █████████████████▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒   46% / 54%   (137 / 163)
wallets      ███████████▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒   34% / 66%   (60 / 114)
dinero USD   ███████████████████▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒   56% / 44%   ($15.5k / $12.3k)
```

Ese ejemplo real: el 46% de las *operaciones* son compras pero solo el 34% de las *wallets* compran, y aun así el 56% del dinero es de compra. Traducido: pocos compradores con ticket grande contra muchos vendedores pequeños. Contarlo solo por operaciones lo habría ocultado.

También detecta wallets que compran y venden en la misma ventana (rotación) y avisa cuando las compras se concentran en pocas manos — compra repetida, no demanda nueva.

## Buscador de tendencias

```bash
python3 pumpscope.py --buscar
```

No busca lo que **más** ha subido — eso lo hace cualquier pantalla de trending, y comprar ahí suele ser comprar el techo. Busca lo que está **empezando** a subir:

- Aceleración de volumen (el tramo reciente pesa más que el anterior)
- Compradores únicos creciendo, no solo número de operaciones
- Movimiento positivo pero **no** parabólico: `>+300%` en 1h se penaliza por entrada tardía
- Las ventanas cortas confirman a las largas y no se han girado todavía
- Liquidez suficiente para poder salir

Cada candidato muestra sus motivos a favor y en contra. **El score no es una probabilidad**: ordena candidatos para mirarlos de cerca.

Dos filtros que costaron descubrir: PumpSwap aloja acciones tokenizadas (HOOD, NVDA) y tokens de empresas que colaban en el ranking con un 99% de compradores, así que se filtra por origen pump.fun. Y ese 99% de compradores se penaliza en vez de premiarse: una subida orgánica tiene gente tomando beneficios; que casi nadie venda suele preceder al primer muro.

## Cómo se calculan las probabilidades

Softmax sobre 3 resultados, donde el sesgo de cada clase es el **log del base rate empírico** y cada señal medida suma o resta desde ahí:

```
P(k) = softmax( log(base_rate_k) + confianza · Σ wᵢₖ · zᵢ )
```

Los base rates no son intuición, salen de datos publicados:

- **68,67%** de los tokens de pump.fun hacen su último trade *el mismo día* del lanzamiento (CoinGecko, 18,67M tokens, ene-2024 → jun-2026)
- **4,55%** siguen operando pasados 90 días
- **~0,26%** gradúan la curva (DEXTools, mediados de 2026)

Cuatro propiedades deliberadas:

- **Sin evidencia, devuelve el base rate.** No inventa una opinión cuando no hay datos.
- **La ausencia de datos no es evidencia.** Si una fuente falla, las señales que dependían de ella se atenúan a cero. Sin este freno, un 429 de la API se leía como "cero wallets participando" y hundía la predicción.
- **Las señales correlacionadas no se cuentan dos veces.** Un token grande y sano tiene a la vez muchas wallets, liquidez profunda y HHI bajo: es *la misma información*. Sumarlas como independientes disparaba el pronóstico de 27% a 70%. Van agrupadas y topadas.
- **Todo es auditable.** `--why` imprime cada señal, su grupo, su z-score y cuántos log-odds movió.

## Señales que mide

| Grupo | Señales |
|---|---|
| estructura | wallets únicas, profundidad de liquidez, concentración HHI, rotación anómala |
| momentum | momentum en sigmas, sobreextensión vs media, caída desde ATH |
| flujo | desbalance compra/venta, decaimiento del ritmo de trades |
| dev | **el creator vendiendo** (cruza el `creator` de pump.fun con las wallets de cada trade) |
| curva | proximidad a graduación, estancamiento |

Los niveles de precio salen de pivotes fractales ponderados por volumen, del ATH, y —en tokens aún en curva— del precio exacto de graduación, que es un imán mecánico del protocolo, no una línea psicológica.

---

## Lo que esto no es

No predice el futuro. Es un modelo estadístico sobre un mercado donde el 68,67% de los tokens muere el mismo día y el 0,26% gradúa. Las probabilidades son condicionales y están **mal calibradas en las colas por definición**: los eventos que más te importan (el 100x, el rug instantáneo) son exactamente los que menos datos tienen.

Lo que sí hace bien es lo aburrido: leer el estado real de la curva, medir la volatilidad de verdad, detectar al dev saliendo, y **decirte cuándo no sabe**. Los avisos de calidad de datos son parte del output, no una nota al pie.

No es consejo financiero.

## Recalibrar

Los priors de `ps/model.py:PRIORS` y los pesos de `WEIGHTS` están puestos como constantes con nombre precisamente para que los cambies si mides algo mejor. Un cambio ahí se propaga a todo el modelo sin tocar nada más.
