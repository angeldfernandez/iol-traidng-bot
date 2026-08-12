# iol-trading-bot

Bot de trading automático para [Invertir Online (IOL)](https://www.invertironline.com/), usando su
[API pública](https://iol.apidocs.ar/). Escanea el mercado (acciones argentinas + CEDEARs, ~950
símbolos entre los paneles configurados) con indicadores técnicos (cruce de medias móviles + RSI) y
opera automáticamente vía `POST /api/operar/Comprar` y `POST /api/operar/Vender`.

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

### Escaneo de mercado en vez de watchlist fija (`iol_bot/market_scanner.py`)

En vez de mantener una lista fija de símbolos, cada ciclo el bot:

1. Trae cotizaciones de los paneles configurados en `PANELES` (default `merval,burcap,cedears` —
   ~20 + ~25 + ~920 símbolos), **una sola llamada HTTP por panel** vía
   `GET /api/Cotizaciones/{Instrumento}/{Panel}/{Pais}`.
2. Deduplica símbolos que aparecen en más de un panel (quedándose con el de mayor volumen visto).
3. Rankea por **volumen nominal operado** (`volumen × último precio`, un proxy de liquidez mejor que
   volumen en unidades, que sesga a favor de acciones baratas) y se queda con los
   `MAX_SIMBOLOS_A_ANALIZAR` (default 50) de mayor volumen.
4. Recién a esos ~50 símbolos les baja serie histórica completa y calcula indicadores — bajarle el
   histórico a los ~950 símbolos del universo completo cada ciclo sería lento y probablemente
   dispare rate-limiting en la API.

Este filtro es una decisión de trade-off: prioriza instrumentos líquidos (spreads más finos, menos
slippage) por sobre cobertura total. Si un símbolo puntual te interesa y queda afuera del top N,
subí `MAX_SIMBOLOS_A_ANALIZAR` o restringí `PANELES` a uno donde ese símbolo tenga más peso relativo.

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

- **Monto máximo por orden** (`MAX_MONTO_POR_ORDEN`).
- **Exposición máxima por símbolo** como % de la cartera total (`MAX_EXPOSICION_POR_SIMBOLO_PCT`),
  calculada contra `GET /api/estadocuenta` y `GET /api/portafolio`.
- **Circuit breaker de pérdida diaria** (`MAX_PERDIDA_DIARIA_PCT`): compara el valor de la cartera
  contra su valor al comienzo del día; si la caída supera el umbral, el bot deja de operar por el
  resto del día (es "sticky": no se reactiva solo aunque la cartera se recupere, hace falta que
  empiece un día nuevo). El estado se persiste en `logs/risk_state.json` para que el dashboard lo
  pueda leer aunque sea un proceso aparte del bot.

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
- La estrategia SMA+RSI es un punto de partida; conviene backtestearla con tus propios datos
  históricos antes de confiar en ella para operar en real.
- Considerá agregar notificaciones (email/Telegram) cuando el circuit breaker se activa o una orden
  es rechazada, para enterarte sin tener que mirar los logs.
- Los nombres de panel (`merval`, `burcap`, `cedears`) los encontré probando contra la API real, no
  están en la documentación oficial (el `Panel` del endpoint es un string libre, no un enum
  documentado). Si en el futuro devuelven 404 o la API cambia esos nombres, revisalo con
  `client.panel_cotizaciones(...)` directo antes de asumir que es un bug del bot.
