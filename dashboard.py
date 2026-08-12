"""Dashboard local de solo lectura para iol-trading-bot.

Corre en tu máquina, nunca envía órdenes (solo hace GET a la API de IOL para mostrar datos en
vivo, y lee los CSV/JSON que main.py va generando en logs/). Uso:

    streamlit run dashboard.py
"""
import pandas as pd
import streamlit as st

from iol_bot.auth import IOLAuth
from iol_bot.client import IOLApiError, IOLClient
from iol_bot.config import Config
from iol_bot.executor import TRADES_LOG
from iol_bot.indicators import rsi, sma
from iol_bot.main import RISK_STATE_PATH, build_posiciones_por_simbolo
from iol_bot.market_data import get_historical_prices_cached
from iol_bot.market_scanner import scan_market
from iol_bot.pnl import daily_pnl_pesos
from iol_bot.price_cache import load_cache
from iol_bot.risk import load_status
from iol_bot.signals_log import SIGNALS_LOG

from scripts.paper_trade import PAPER_PORTFOLIO_PATH, PAPER_SIGNALS_LOG, PAPER_TRADES_LOG
from iol_bot.paper_portfolio import load_status as load_paper_status

st.set_page_config(page_title="iol-trading-bot", layout="wide")


@st.cache_resource
def get_client_and_config():
    config = Config.load()
    auth = IOLAuth(config.base_url, config.username, config.password)
    return IOLClient(config.base_url, auth), config


@st.cache_data(ttl=30)
def fetch_account_snapshot():
    client, _ = get_client_and_config()
    estado_cuenta = client.estado_cuenta()
    portafolio = client.portafolio()
    return estado_cuenta, portafolio


@st.cache_data(ttl=60)
def fetch_price_history(simbolo, mercado, hoy_precio=None):
    client, _ = get_client_and_config()
    return get_historical_prices_cached(client, simbolo, mercado=mercado, hoy_precio=hoy_precio)


@st.cache_data(ttl=120)
def fetch_watchlist():
    client, config = get_client_and_config()
    return scan_market(client, paneles=config.paneles, top_n=config.max_simbolos_a_analizar)


def read_csv_log(path, columns):
    if not path.exists():
        return pd.DataFrame(columns=columns)
    return pd.read_csv(path)


def ultimo_precio_cacheado(simbolo, mercado="bCBA"):
    """Último precio conocido del cache local (cache/{mercado}_{simbolo}.csv) — el mismo dato que
    ya usa el bot para calcular indicadores, así que no cuesta ninguna llamada extra a la API.
    None si todavía no hay cache para ese símbolo."""
    df = load_cache(simbolo, mercado)
    if df.empty:
        return None
    return float(df["cierre"].iloc[-1])


st.title("iol-trading-bot — Dashboard")

try:
    client, config = get_client_and_config()
except ValueError as exc:
    st.error(f"No se pudo cargar la configuración: {exc}")
    st.stop()

modo = "🔴 LIVE (dinero real)" if not config.effective_dry_run else "🟢 DRY_RUN (simulado)"
st.caption(f"Modo actual del bot según .env: **{modo}**  |  Fuente de datos: cuenta real de IOL (solo lectura acá)")

if st.button("🔄 Refrescar datos"):
    st.cache_data.clear()

# --- Paper trading (cartera virtual) ---
st.header("📝 Paper trading — cartera virtual")
st.caption(
    "Corré `python -m scripts.paper_trade` para que esta sección tenga datos. Usa precios reales "
    "de mercado pero nunca envía órdenes reales — el cash y las posiciones son 100% virtuales, "
    "separados por completo de tu cuenta real de IOL. La ganancia/pérdida de cada posición se "
    "calcula con el último precio cacheado localmente (el mismo que usa el bot), no con el costo "
    "de compra — refleja lo no realizado de verdad."
)
paper_status = load_paper_status(PAPER_PORTFOLIO_PATH)
if paper_status is None:
    st.info("Todavía no corriste ninguna sesión de paper trading — no hay estado guardado.")
else:
    cash = paper_status.get("cash", 0.0)
    positions = paper_status.get("positions", {})
    initial_cash = paper_status.get("initial_cash", 0.0)

    # Último precio real por símbolo desde el cache local (mismo dato que usa el bot para calcular
    # indicadores) — no cuesta llamadas extra, y a diferencia de aproximar a costo promedio, sí
    # refleja la ganancia/pérdida no realizada de verdad.
    precios_cache = {simbolo: ultimo_precio_cacheado(simbolo) for simbolo in positions}
    valorizado = cash + sum(
        p["cantidad"] * (precios_cache[simbolo] if precios_cache[simbolo] is not None else p["costo_promedio"])
        for simbolo, p in positions.items()
    )
    pnl = valorizado - initial_cash
    pnl_pct = (pnl / initial_cash * 100) if initial_cash else 0

    col1, col2, col3 = st.columns(3)
    col1.metric("Cartera virtual inicial", f"${initial_cash:,.2f}")
    col2.metric("Cash disponible", f"${cash:,.2f}")
    col3.metric(
        "Valorizado total (a último precio)",
        f"${valorizado:,.2f}",
        delta=f"{pnl:,.2f} ({pnl_pct:.2f}%)",
    )
    st.caption(f"Última actualización: {paper_status.get('updated_at', '—')}")

    if positions:
        filas_paper = []
        for simbolo, p in positions.items():
            precio_actual = precios_cache[simbolo]
            if precio_actual is None:
                filas_paper.append(
                    {
                        "símbolo": simbolo,
                        "cantidad": p["cantidad"],
                        "costo promedio": p["costo_promedio"],
                        "último precio": "sin cache todavía",
                        "ganancia %": None,
                        "ganancia $": None,
                    }
                )
                continue
            ganancia_pct = (precio_actual - p["costo_promedio"]) / p["costo_promedio"] * 100 if p["costo_promedio"] else 0
            filas_paper.append(
                {
                    "símbolo": simbolo,
                    "cantidad": p["cantidad"],
                    "costo promedio": p["costo_promedio"],
                    "último precio": precio_actual,
                    "ganancia %": round(ganancia_pct, 2),
                    "ganancia $": round((precio_actual - p["costo_promedio"]) * p["cantidad"], 2),
                }
            )
        st.dataframe(pd.DataFrame(filas_paper), use_container_width=True, hide_index=True)
    else:
        st.info("Sin posiciones abiertas en la cartera virtual.")

    paper_trades_df = read_csv_log(
        PAPER_TRADES_LOG, ["timestamp", "simbolo", "lado", "cantidad", "precio", "motivo", "resultado"]
    )
    if not paper_trades_df.empty:
        st.write("**Operaciones virtuales ejecutadas**")
        st.dataframe(paper_trades_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)

    paper_signals_df = read_csv_log(
        PAPER_SIGNALS_LOG, ["timestamp", "simbolo", "signal", "precio", "motivo", "estado"]
    )
    if not paper_signals_df.empty:
        with st.expander(f"Ver las {len(paper_signals_df)} señales de paper trading (ejecutadas o no)"):
            st.dataframe(
                paper_signals_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True
            )

# --- Cartera y P&L (cuenta real) ---
st.header("Cartera y P&L — cuenta real de IOL")
try:
    estado_cuenta, portafolio = fetch_account_snapshot()
except IOLApiError as exc:
    st.error(f"Error consultando la API de IOL: {exc}")
    st.stop()

total_en_pesos = estado_cuenta.get("totalEnPesos", 0.0)
activos = portafolio.get("activos", [])

ganado_hoy = sum(daily_pnl_pesos(a.get("valorizado", 0), a.get("variacionDiaria", 0)) for a in activos)

col1, col2 = st.columns(2)
col1.metric("Valor total de la cartera (ARS)", f"${total_en_pesos:,.2f}")
col2.metric(
    "Ganado/perdido HOY (posiciones abiertas)",
    f"${ganado_hoy:,.2f}",
    delta=f"{ganado_hoy:,.2f}",
    delta_color="normal",
)
st.caption(
    "Calculado a partir de la variación % de precio de hoy de cada posición. No incluye "
    "operaciones ya cerradas hoy ni movimientos de efectivo — para eso ver el circuit breaker abajo, "
    "que compara el valor total de la cuenta contra el inicio del día."
)

if activos:
    filas = []
    for a in activos:
        filas.append(
            {
                "símbolo": a["titulo"]["simbolo"],
                "cantidad": a.get("cantidad", 0),
                "precio compra promedio": a.get("ppc", 0),
                "último precio": a.get("ultimoPrecio", 0),
                "valorizado": a.get("valorizado", 0),
                "variación hoy %": a.get("variacionDiaria", 0),
                "ganado/perdido hoy $": round(daily_pnl_pesos(a.get("valorizado", 0), a.get("variacionDiaria", 0)), 2),
                "ganancia total $": a.get("gananciaDinero", 0),
                "ganancia total %": a.get("gananciaPorcentaje", 0),
            }
        )
    st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
else:
    st.info("No hay posiciones abiertas en la cartera.")

# --- Circuit breaker ---
st.header("Circuit breaker de pérdida diaria")
risk_status = load_status(RISK_STATE_PATH)
if risk_status is None:
    st.info("El bot todavía no corrió ningún ciclo hoy (no hay estado de riesgo persistido).")
else:
    col1, col2, col3 = st.columns(3)
    col1.metric("Estado", "🛑 DETENIDO" if risk_status["halted"] else "✅ Operando normal")
    col2.metric("P&L del día (ARS)", f"${risk_status['daily_pnl']:,.2f}")
    limite = risk_status["max_perdida_diaria_pct"]
    baseline = risk_status.get("baseline_value") or 0
    pct_actual = (-risk_status["daily_pnl"] / baseline * 100) if baseline else 0
    col3.metric("Pérdida del día vs. límite", f"{pct_actual:.1f}% / {limite:.1f}%")
    st.caption(f"Última actualización: {risk_status.get('updated_at', '—')}")

# --- Señales generadas ---
st.header("Señales generadas (ejecutadas o no)")
signals_df = read_csv_log(SIGNALS_LOG, ["timestamp", "simbolo", "signal", "precio", "motivo", "estado"])
if signals_df.empty:
    st.info("Todavía no hay señales registradas — corré el bot o `scripts/smoke_test.py`.")
else:
    filtro_simbolos = st.multiselect("Filtrar por símbolo", sorted(signals_df["simbolo"].unique()))
    df_mostrado = signals_df if not filtro_simbolos else signals_df[signals_df["simbolo"].isin(filtro_simbolos)]
    st.dataframe(df_mostrado.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)

# --- Operaciones ejecutadas ---
st.header("Operaciones ejecutadas o simuladas")
trades_df = read_csv_log(TRADES_LOG, ["timestamp", "modo", "simbolo", "lado", "cantidad", "precio", "motivo", "resultado"])
if trades_df.empty:
    st.info("Todavía no hay operaciones registradas.")
else:
    st.dataframe(trades_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)

# --- Gráfico de precio + indicadores ---
st.header("Precio e indicadores por símbolo")
try:
    watchlist = fetch_watchlist()
except IOLApiError as exc:
    watchlist = []
    st.error(f"Error escaneando el mercado: {exc}")

simbolos = [item["simbolo"] for item in watchlist]
if simbolos:
    st.caption(
        f"Universo actual: {len(simbolos)} símbolos de mayor volumen entre los paneles {config.paneles} "
        f"(top {config.max_simbolos_a_analizar} configurado en MAX_SIMBOLOS_A_ANALIZAR)."
    )
    simbolo_elegido = st.selectbox("Símbolo", simbolos)
    item_elegido = next(item for item in watchlist if item["simbolo"] == simbolo_elegido)

    price_df = fetch_price_history(simbolo_elegido, item_elegido["mercado"], item_elegido.get("ultimo_precio"))
    if price_df.empty:
        st.warning("Sin datos históricos para este símbolo todavía.")
    else:
        chart_df = price_df.set_index("fecha")[["cierre"]].copy()
        chart_df["SMA rápida (20)"] = sma(price_df["cierre"], 20).values
        chart_df["SMA lenta (50)"] = sma(price_df["cierre"], 50).values
        st.line_chart(chart_df.rename(columns={"cierre": "precio"}))

        rsi_series = rsi(price_df["cierre"], 14)
        rsi_df = pd.DataFrame({"RSI": rsi_series.values}, index=price_df["fecha"])
        st.line_chart(rsi_df)
        st.caption("RSI: sobrecompra ≥ 70, sobreventa ≤ 30 (referencia, no se dibujan líneas de umbral).")
else:
    st.info("El scan de mercado no devolvió símbolos — revisá PANELES en tu .env.")
