# iol-trading-bot

Bot de trading automático para [Invertir Online (IOL)](https://www.invertironline.com/), usando su
[API pública](https://iol.apidocs.ar/). Escanea el mercado (acciones argentinas + CEDEARs, ~950
símbolos entre los paneles configurados), preselecciona por liquidez y los rankea con un motor de
scoring cuantitativo (momentum, tendencia, volatilidad, volumen, fuerza relativa, riesgo — ver
"Motor de scoring/ranking" más abajo), evalúa la watchlist resultante con indicadores técnicos
(cruce de medias móviles + RSI) y opera automáticamente vía `POST /api/operar/Comprar` y
`POST /api/operar/Vender`.

## ⚠️ Advertencia de riesgo — leé esto primero

Este bot puede enviar **órdenes reales con dinero real** de forma completamente autónoma, sin
confirmación manual. Ningún indicador técnico garantiza resultados. Los límites de riesgo en
`.env` (`MAX_MONTO_POR_ORDEN`, `MAX_EXPOSICION_POR_SIMBOLO_PCT`, `MAX_PERDIDA_DIARIA_PCT`) reducen
el daño potencial de un bug o una mala racha, pero **no eliminan el riesgo de pérdida de capital**.

**La API de IOL no tiene ningún entorno de pruebas con dinero falso.** Ni existe un sandbox por API,
ni la Cuenta de práctica del Simulador es alcanzable por API (son dos sistemas separados — más
detalle abajo). Esto significa que **no hay forma de probar el envío real de órdenes sin arriesgar
dinero de verdad**. La única red de seguridad genuina es `DRY_RUN=true` (que directamente no llama a
`Comprar`/`Vender`), reforzada por `CONFIRM_LIVE_TRADING`. Revisá a fondo `logs/trades.csv` en modo
simulado antes de siquiera considerar pasar a real, y cuando lo hagas, arrancá con montos mínimos.

## Requisitos previos

1. Cuenta de IOL con el producto **API** habilitado: *Mi Cuenta > Personalización > APIs*, aceptar
   los términos del servicio.
2. Python 3.11+.

## Instalación

```bash
cd iol-trading-bot
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env       # Windows (o `cp` en bash)
```

Completá `.env` con tu usuario/contraseña de IOL y ajustá los paneles a escanear y los límites de riesgo.

## Por qué no hay forma de probar con dinero falso

Investigué dos posibles vías y ninguna sirve para este bot:

- **Sandbox por API**: no existe (ni aparece en tu cuenta, ni resuelve el subdominio que mencionaba
  documentación de terceros).
- **Cuenta de práctica / Simulador** (*Herramientas > Simulador*, $100.000 virtuales): existe, pero
  según la propia documentación de IOL esa cuenta **"no está vinculada a tu usuario ni a los
  servicios de API de InvertirOnline.com"**. Es un entorno solo-web, separado del sistema de API por
  completo — no hay credenciales propias que apunten ahí vía API porque no está conectada a la API.

Conclusión práctica: `IOL_USERNAME`/`IOL_PASSWORD` en `.env` son las de tu **cuenta real**, y
cualquier llamada a `Comprar`/`Vender` que el bot haga (con `DRY_RUN=false` y `CONFIRM_LIVE_TRADING=true`)
mueve dinero real. `DRY_RUN=true` es la única simulación posible — no llama a esos endpoints en
absoluto, solo calcula y loguea qué habría hecho.

## Cómo correrlo

```bash
python -m iol_bot.main
```

El loop principal corre solo en horario de mercado de BYMA (11:00–17:00, hora Argentina, de lunes a
viernes — ajustable en `iol_bot/main.py` si hace falta). En cada ciclo re-escanea el mercado (ver
sección siguiente) y evalúa el universo resultante.

### De universo a watchlist: scan de liquidez + motor de scoring/ranking

En vez de mantener una lista fija de símbolos, cada ciclo el bot arma la watchlist en dos etapas:

1. **Preselección barata por liquidez** (`iol_bot/market_scanner.py::scan_market_candidates`): trae
   cotizaciones de los paneles configurados en `PANELES` (default `merval,burcap,cedears` — ~20 +
   ~25 + ~920 símbolos), **una sola llamada HTTP por panel** vía
   `GET /api/Cotizaciones/{Instrumento}/{Panel}/{Pais}`, deduplica símbolos repetidos entre paneles
   (quedándose con el de mayor volumen visto) y se queda con los `funnel.candidate_pool_size`
   (`config/scoring.yaml`, default 150) de mayor **volumen nominal operado**
   (`volumen × último precio` — mejor proxy de liquidez que volumen en unidades, que sesga a favor
   de acciones baratas). Esto no cuesta llamadas de serie histórica.
2. **Motor de scoring/ranking** (`iol_bot/ranking.py`, `scoring.py`, `features.py`): a ese pool de
   candidatos SÍ se les baja serie histórica (con el mismo cache de siempre) y se les calcula un set
   amplio de features — momentum, tendencia, momentum técnico (RSI/MACD), volatilidad (ATR),
   volumen, fuerza relativa contra un benchmark (`SPY` por defecto), y performance ajustada por
   riesgo (Sharpe/Sortino/Calmar). Un filtro de calidad (historia mínima, volumen mínimo, precio
   mínimo) marca cada candidato como elegible o no **sin descartarlo en silencio**: el motivo queda
   guardado igual. Cada feature se normaliza por percentil dentro del pool elegible del día y se
   combina en un `score_total` 0-100 con los pesos de `config/scoring.yaml`. Los
   `funnel.top_n_final` (default 50) de mayor score son la watchlist final que recibe la estrategia.

Bajarle el histórico a los ~950 símbolos del universo completo cada ciclo sería lento y probablemente
dispare rate-limiting en la API — por eso la preselección por liquidez sigue existiendo como primer
filtro barato antes de scorear. Todos los pesos, ventanas y umbrales están en `config/scoring.yaml`,
no hardcodeados en el código.

**Ranking histórico**: cada corrida persiste `rankings/{YYYY-MM-DD}.csv` con TODOS los candidatos
evaluados ese día (elegibles o no). `iol_bot/ranking.py` expone `get_current_ranking`,
`get_previous_ranking`, `diff_rankings` (entradas/salidas del TOP y evolución de score/rank) y
`get_symbol_history` para consultar cómo evolucionó un símbolo en el tiempo. El archivo de HOY se
reescribe en cada ciclo (es un valor vivo hasta que cierra el mercado, no una foto de fin de día).
`rankings/` es local y descartable, igual que `cache/` — está en `.gitignore`.

Si un símbolo puntual te interesa y queda afuera del pool de candidatos, subí
`funnel.candidate_pool_size` en `config/scoring.yaml` o restringí `PANELES` a uno donde ese símbolo
tenga más peso relativo.

### Costo de la API y cache local (`iol_bot/price_cache.py`)

La API de IOL da **25.000 llamadas gratis por mes**; superado eso cobra $500 + IVA (se acredita
como bonificación de comisiones a 30 días — no hace falta llegar a esa situación).
[Tarifas oficiales](https://www.invertironline.com/tarifas).

Pedir la serie histórica completa (180 días) de 50 símbolos en cada ciclo sería carísimo en
llamadas — casi todo ese histórico no cambió desde el ciclo anterior, solo se actualiza la barra de
HOY. Por eso `get_historical_prices_cached`:

- Guarda el histórico de cada símbolo en `cache/{mercado}_{simbolo}.csv`.
- En el primer ciclo del día (o si el cache está vacío/desactualizado por más de 5 días) pide la
  serie completa una vez y la cachea.
- En los ciclos siguientes del mismo día, actualiza la barra de HOY con el `ultimoPrecio` que **ya
  trajo gratis** el scan de paneles — sin volver a llamar a `serie_historica`.

Validado contra la cuenta real: primer ciclo 15/15 símbolos llaman a la API, segundo ciclo del
mismo día **0/15**. En la práctica baja el consumo de ~1.320 a ~170 llamadas/día con la config
default (top 50 símbolos, ciclo cada 15 min) — de ~28.000/mes (por encima del cupo gratis) a
~3.600/mes. La carpeta `cache/` es local y descartable (está en `.gitignore`) — borrarla solo hace
que el próximo ciclo vuelva a pedir todo completo, no rompe nada.

**Con el motor de scoring/ranking** el pool que recibe serie histórica es
`funnel.candidate_pool_size` (default 150 en `config/scoring.yaml`), no los 50 de antes — sigue
siendo el mismo mecanismo de cache (0 llamadas nuevas por símbolo ya cacheado el mismo día), pero el
primer ciclo del día para símbolos nuevos sube proporcionalmente (150 vs. 50). Con la config default
eso son ~150 llamadas una vez por día hábil ≈ 3.300/mes solo por esa parte — sigue muy por debajo del
cupo gratis, pero si subís mucho `candidate_pool_size` conviene volver a hacer la cuenta.

### Flujo recomendado antes de operar en real

0. **Prueba rápida** (no espera al horario de mercado ni al loop): `python -m scripts.smoke_test`.
   Hace login, trae estado de cuenta/portafolio, escanea el mercado, y para una muestra de 10
   símbolos de mayor volumen trae cotización + histórico y calcula la señal. Es de solo lectura —
   nunca llama a `Comprar`/`Vender`, así que es segura correrla las veces que quieras sin importar
   `DRY_RUN`/`CONFIRM_LIVE_TRADING`.
1. `DRY_RUN=true` (default): corré el bot con tus credenciales reales de todos modos — el login,
   las cotizaciones y el histórico son solo lectura, no arriesgan nada. Revisá `logs/bot.log` y
   `logs/trades.csv` para ver qué habría comprado/vendido, sin enviar nada.
2. Dejalo correr así varios días de mercado. Validá que las señales generadas tienen sentido para
   vos y que el dimensionamiento de las órdenes (`risk.py`) es el que esperás.
3. Recién cuando estés conforme: bajá mucho `MAX_MONTO_POR_ORDEN` (para que el primer error posible
   salga barato), poné `DRY_RUN=false` **y** `CONFIRM_LIVE_TRADING=true` (hacen falta los dos), y
   mirá de cerca las primeras órdenes reales que salgan. Este cambio lo hacés vos explícitamente — el
   bot nunca lo activa solo, y con cualquiera de los dos flags en su valor seguro sigue simulando.

## Paper trading con cartera virtual (`scripts/paper_trade.py`)

```bash
python -m scripts.paper_trade
```

Corre la misma estrategia y los mismos límites de riesgo que el bot real, pero contra una **cartera
100% virtual** que arranca con `PAPER_INITIAL_CASH` (default $100.000 ARS) en vez de tu cuenta real
— útil porque `DRY_RUN` con tu cuenta real de verdad (`estadocuenta().totalEnPesos`) da montos poco
representativos si tu cuenta tiene poco capital. Usa precios reales de mercado (solo lectura) pero
**nunca llama a `Comprar`/`Vender`** — las compras/ventas se resuelven en memoria en
`iol_bot/paper_portfolio.py` y se persisten en `logs/paper_portfolio.json`.

Corre ciclos hasta que cierra el mercado (horario de BYMA) y al final imprime un resumen: cash,
posiciones abiertas, valorizado total y P&L vs. los $100.000 iniciales. Pensado para dejarlo
corriendo en background durante la sesión (`... &` o `run_in_background` si lo lanza un agente) y
mirar el progreso en el dashboard mientras tanto. Logs completamente separados de los del bot real:
`logs/paper_trades.csv`, `logs/paper_signals.csv`, `logs/paper_risk_state.json` — nunca se mezclan
con `logs/trades.csv` ni con una corrida real de `main.py`.

## Estrategia incluida

`iol_bot/strategy.py` implementa un cruce de SMA rápida/lenta (por defecto 20/50) confirmado con RSI
(14): compra en cruce alcista si el RSI no está sobrecomprado, vende en cruce bajista o si el RSI
entra en sobrecompra (toma de ganancias). Es un punto de partida — `Strategy` es una interfaz
intercambiable, se pueden agregar otras estrategias sin tocar el resto del bot.

## Gestión de riesgo (`iol_bot/risk.py`)

Antes de cualquier orden se aplican estos límites (configurables en `.env`):

- **Monto máximo por orden, como % de la cartera** (`MAX_MONTO_POR_ORDEN_PCT`, default 20% — igual
  al de exposición por símbolo de abajo). No es un monto fijo en ARS: escala solo con el tamaño de
  la cartera, así no deja afuera sin querer instrumentos que cotizan caro por unidad.
- **Exposición máxima por símbolo** como % de la cartera total (`MAX_EXPOSICION_POR_SIMBOLO_PCT`),
  calculada contra `GET /api/estadocuenta` y `GET /api/portafolio`.
- **Circuit breaker de pérdida diaria** (`MAX_PERDIDA_DIARIA_PCT`): compara el valor de la cartera
  contra su valor al comienzo del día; si la caída supera el umbral, el bot deja de operar por el
  resto del día (es "sticky": no se reactiva solo aunque la cartera se recupere, hace falta que
  empiece un día nuevo). El estado se persiste en `logs/risk_state.json` para que el dashboard lo
  pueda leer aunque sea un proceso aparte del bot.

## Motor de scoring/ranking (`config/scoring.yaml`)

Toda la configuración "de investigación cuantitativa" (a diferencia de la operativa, que vive en
`.env`) está en `config/scoring.yaml`:

- `funnel`: tamaño del pool de candidatos (`candidate_pool_size`) y del TOP final (`top_n_final`).
- `quality_filters`: historia mínima, volumen nominal mínimo, precio mínimo. **No incluye filtro de
  spread** (`MAX_SPREAD_PERCENT`) — ningún endpoint de IOL que usa este bot expone punta
  compradora/vendedora, así que ese dato no existe hoy para calcularlo.
- `relative_strength`: símbolo/mercado usado como benchmark para fuerza relativa (default `SPY` en
  `bCBA`) — se excluye automáticamente del pool de candidatos para no competir contra sí mismo.
- `groups` / `weights`: qué features (`iol_bot/features.py`) integran cada subscore y qué peso tiene
  cada grupo en el `score_total` (deben sumar 1.0 — `ScoringConfig.load()` valida esto al arrancar y
  también que no haya grupos en `weights` sin definir en `groups`).
- `directionality_lower_is_better`: features donde un valor crudo más bajo puntúa mejor (ej.
  volatilidad, distancia al máximo).

La normalización de cada feature es por **percentil dentro del pool elegible del día** (no contra un
umbral absoluto) — el score de un símbolo depende de cómo está parado ese día frente a los demás
candidatos. Con pools chicos (mercado recién abierto, filtros muy restrictivos) el percentil pierde
resolución; `iol_bot/ranking.py` loggea un warning cuando eso pasa.

**Ventanas de historia**: el cache de precios guarda ~180 días corridos (~120-125 ruedas en el
mercado argentino). Por eso los features de este motor llegan como máximo a ventanas de 60-120
ruedas — SMA200, momentum a 252 días y máximo/mínimo de 52 semanas quedan pendientes para cuando se
amplíe esa ventana de cache (ver "Qué falta" más abajo).

## Dashboard

```bash
streamlit run dashboard.py
```

App local de solo lectura (nunca llama a `Comprar`/`Vender`, solo hace `GET` a la API y lee los
logs que `main.py` va generando). Muestra:

- **Cartera y P&L**: posiciones actuales, valorizado, ganado/perdido HOY por posición (calculado con
  `variacionDiaria`) y ganancia total $/% desde la compra (en vivo desde la API).
- **Circuit breaker**: si el bot está operando normal o detenido, y cuánto margen de pérdida diaria queda.
- **Señales generadas**: todas las que calculó la estrategia, se hayan ejecutado o no (`logs/signals.csv`),
  filtrables por símbolo — para entender *por qué* el bot actuó o no actuó.
- **Operaciones ejecutadas o simuladas** (`logs/trades.csv`).
- **Ranking TOP 50**: tabla del motor de scoring de hoy (score total + subscores por símbolo),
  entradas/salidas vs. el ranking anterior, candidatos elegibles fuera del TOP, y candidatos
  excluidos con el motivo. Tarda más que el resto del dashboard porque recalcula el ranking del
  pool completo de candidatos, no solo el TOP final.
- **Gráfico de precio + SMA rápida/lenta + RSI** por símbolo, eligiendo entre el universo actual del
  scan de mercado (top `MAX_SIMBOLOS_A_ANALIZAR` por volumen).

Corre 100% local — no es un artifact hosteado, porque muestra datos reales de tu cuenta de IOL.
Usa las mismas credenciales de `.env`, así que solo vos podés levantarlo. El botón "Refrescar datos"
limpia el caché (30-60s por defecto) para traer datos más recientes.

## Tests

```bash
pytest
```

Todos los tests usan HTTP mockeado (`responses`) — no requieren credenciales ni conexión real a IOL.

## Nota sobre el MCP oficial de IOL

IOL tiene un [MCP](https://www.invertironline.com/mcp) para conectar tu cuenta a asistentes de IA
(Claude) por conversación. No es una alternativa a este bot: IOL aclara explícitamente que **ninguna
orden se envía automáticamente** vía MCP, siempre requiere confirmación manual tuya. Sirve para
consultar cuenta/cotizaciones u operar a mano charlando con un asistente, no para ejecución autónoma.

## Qué falta / próximos pasos posibles

- El arranque/reinicio automático del bot (Windows Task Scheduler, servicio, VM) no está incluido —
  hoy se corre manualmente con `python -m iol_bot.main`.
- La estrategia SMA+RSI en sí (`iol_bot/strategy.py`) no cambió con el motor de scoring/ranking —
  sigue siendo la misma señal técnica simple, ahora aplicada sobre una watchlist mejor seleccionada.
  Conviene backtestearla con tus propios datos históricos antes de confiar en ella para operar en real.
- Considerá agregar notificaciones (email/Telegram) cuando el circuit breaker se activa o una orden
  es rechazada, para enterarte sin tener que mirar los logs.
- Los nombres de panel (`merval`, `burcap`, `cedears`) los encontré probando contra la API real, no
  están en la documentación oficial (el `Panel` del endpoint es un string libre, no un enum
  documentado). Si en el futuro devuelven 404 o la API cambia esos nombres, revisalo con
  `client.panel_cotizaciones(...)` directo antes de asumir que es un bug del bot.

### Pendiente del motor cuantitativo (deliberadamente fuera de esta fase)

El motor de scoring/ranking (`iol_bot/ranking.py`, `scoring.py`, `features.py`) cubre selección y
ranking de candidatos. Lo que sigue quedó explícitamente afuera de esta fase, para no meter todo de
una vez en un bot que opera con dinero real sin sandbox:

- **Régimen de mercado** (BULL/NEUTRAL/BEAR/HIGH_VOLATILITY) para modular agresividad.
- **Matriz de correlación** entre candidatos y **límites de exposición por sector** — hoy no hay
  fuente de datos de sector para los símbolos de IOL, habría que resolver eso primero (mapeo manual
  o algún endpoint que lo exponga).
- **Setups de entrada** (breakout/pullback/momentum/reversal) — hoy la única señal de entrada sigue
  siendo el cruce SMA+RSI de `strategy.py`, sin usar el `score_total` del ranking para decidir cuándo
  entrar, solo para decidir el universo a evaluar.
- **Stops por ATR y position sizing por riesgo** — `risk.py` sigue usando take-profit/stop-loss de
  % fijo y monto fijo por orden. `features.py` ya calcula `atr_pct`, pero no está conectado a
  `risk.py` todavía.
- **Motor de backtesting** (con costos, sin look-ahead bias) y **walk-forward** — no existe. Es
  probablemente el paso más importante antes de cambiar cualquier lógica de entrada/salida real,
  incluido usar el `score_total` para decidir señales.
- **Premium/descuento CEDEAR vs. subyacente**, **Monte Carlo**, **Machine Learning** — no
  implementados.
- **Base de datos, API REST propia, frontend React, Docker** — se descartó deliberadamente esa
  dirección arquitectónica por ahora; el bot sigue siendo Python + Streamlit + archivos planos
  (`cache/`, `logs/`, `rankings/`), pensado para uso individual, no como plataforma multi-usuario.
