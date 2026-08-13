"""Dashboard local de solo lectura para iol-trading-bot.

Corre en tu máquina, nunca envía órdenes (solo hace GET a la API de IOL para mostrar datos en
vivo, y lee los CSV/JSON que main.py va generando en logs/). Uso:

    streamlit run dashboard.py
"""
import json
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from streamlit_autorefresh import st_autorefresh

from iol_bot.auth import IOLAuth
from iol_bot.client import IOLApiError, IOLClient
from iol_bot.config import Config
from iol_bot.executor import TRADES_LOG
from iol_bot.indicators import rsi, sma
from iol_bot.main import RISK_STATE_PATH
from iol_bot.market_data import get_historical_prices_cached
from iol_bot.market_scanner import scan_market
from iol_bot.paper_portfolio import load_status as load_paper_status
from iol_bot.pnl import daily_pnl_pesos
from iol_bot.price_cache import load_cache
from iol_bot.ranking import build_daily_ranking, diff_rankings, get_current_ranking, get_previous_ranking
from iol_bot.risk import load_status
from iol_bot.scoring_config import ScoringConfig
from iol_bot.signals_log import SIGNALS_LOG
from scripts.paper_trade import PAPER_PORTFOLIO_PATH, PAPER_RISK_STATE_PATH, PAPER_SIGNALS_LOG, PAPER_TRADES_LOG
from scripts.run_backtest import BACKTESTS_DIR

st.set_page_config(page_title="iol-trading-bot", layout="wide", page_icon="📊")

# Paleta validada (colorblind-safe, ver skill de dataviz) — un color por rol, nunca ciclada.
COLOR_PRECIO = "#2a78d6"  # categórico slot 1 (blue)
COLOR_SMA_RAPIDA = "#eb6834"  # slot 2 (orange)
COLOR_SMA_LENTA = "#1baf7a"  # slot 3 (aqua)
COLOR_RSI = "#4a3aa7"  # slot 7 (violet) — panel separado, deliberadamente distinto del de precio
COLOR_ESTRATEGIA = "#2a78d6"  # slot 1 (blue)
COLOR_BENCHMARK = "#eb6834"  # slot 2 (orange)
COLOR_GOOD = "#0ca30c"
COLOR_CRITICAL = "#d03b3b"
COLOR_MUTED = "#898781"


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


@st.cache_data(ttl=120)
def fetch_ranking():
    """Recalcula el ranking de hoy (motor de scoring de iol_bot/ranking.py) y devuelve
    (ranking_hoy, ranking_anterior). Tarda más que fetch_watchlist porque trae/calcula features
    para todo el pool de candidatos configurado (no solo el TOP final)."""
    client, config = get_client_and_config()
    build_daily_ranking(client, config)
    return get_current_ranking(), get_previous_ranking()


def read_csv_log(path, columns):
    if not path.exists():
        return pd.DataFrame(columns=columns)
    return pd.read_csv(path)


@st.cache_data(ttl=60)
def precio_actual_para_dashboard(simbolo, mercado="bCBA"):
    """Último precio para mostrar en el dashboard. Si el cache local
    (cache/{mercado}_{simbolo}.csv) ya está actualizado a HOY, lo usa tal cual — el mismo dato que
    ya usa el bot, sin gastar una llamada extra. Si no (ej. una posición que cayó del pool de
    candidatos del ranking y dejó de actualizarse sola — le pasó a PATH el 2026-08-12, se quedó
    congelada en el precio de entrada todo el día), pide la cotización en vivo: una sola llamada
    por símbolo por minuto (cacheado acá mismo), no en cada refresco del dashboard.
    None solo si ninguna de las dos fuentes tiene dato."""
    df = load_cache(simbolo, mercado)
    if not df.empty and df["fecha"].max().date() == date.today():
        return float(df["cierre"].iloc[-1])

    client, _ = get_client_and_config()
    try:
        cot = client.cotizacion(simbolo, mercado=mercado)
        precio = cot.get("ultimoPrecio")
        if precio:
            return float(precio)
    except IOLApiError:
        pass

    return float(df["cierre"].iloc[-1]) if not df.empty else None


def _listar_backtests():
    if not BACKTESTS_DIR.exists():
        return []
    return sorted((p.parent.name for p in BACKTESTS_DIR.glob("*/summary.json")), reverse=True)


st.title("📊 iol-trading-bot — Dashboard")

try:
    client, config = get_client_and_config()
except ValueError as exc:
    st.error(f"No se pudo cargar la configuración: {exc}")
    st.stop()

modo = "🔴 LIVE (dinero real)" if not config.effective_dry_run else "🟢 DRY_RUN (simulado)"
st.caption(f"Modo actual del bot según .env: **{modo}**  |  Fuente de datos: cuenta real de IOL (solo lectura acá)")

col_refresh, col_auto = st.columns([1, 2])
with col_refresh:
    if st.button("🔄 Refrescar datos"):
        st.cache_data.clear()
with col_auto:
    auto_refresh = st.checkbox("⏱️ Auto-refresh cada 60s", value=True)
if auto_refresh:
    st_autorefresh(interval=60_000, key="auto_refresh_timer")

tab_resumen, tab_paper, tab_ranking, tab_backtests, tab_actividad, tab_simbolo = st.tabs(
    ["📊 Resumen", "📝 Paper Trading", "🏆 Ranking TOP 50", "📈 Backtests", "📋 Actividad", "🔍 Símbolo"]
)

# ============================== Resumen (cuenta real) ==============================
with tab_resumen:
    st.subheader("Cartera y P&L — cuenta real de IOL")
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
    col2.metric("Ganado/perdido HOY (posiciones abiertas)", f"${ganado_hoy:,.2f}", delta=f"{ganado_hoy:,.2f}")
    st.caption(
        "Calculado a partir de la variación % de precio de hoy de cada posición. No incluye "
        "operaciones ya cerradas hoy ni movimientos de efectivo — para eso ver el circuit breaker abajo, "
        "que compara el valor total de la cuenta contra el inicio del día."
    )

    if activos:
        filas = [
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
            for a in activos
        ]
        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
    else:
        st.info("No hay posiciones abiertas en la cartera.")

    st.divider()
    st.subheader("Circuit breaker de pérdida diaria")
    risk_status = load_status(RISK_STATE_PATH)
    if risk_status is None:
        st.info("El bot todavía no corrió ningún ciclo hoy (no hay estado de riesgo persistido).")
    else:
        detenido = risk_status["halted"]
        color_estado = COLOR_CRITICAL if detenido else COLOR_GOOD
        texto_estado = "🛑 DETENIDO" if detenido else "✅ Operando normal"

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(
                f"<div style='color:{color_estado}; font-weight:700; font-size:1.4rem;'>{texto_estado}</div>"
                "<div style='color:#898781; font-size:0.85rem;'>Estado</div>",
                unsafe_allow_html=True,
            )
        col2.metric("P&L del día (ARS)", f"${risk_status['daily_pnl']:,.2f}")
        limite = risk_status["max_perdida_diaria_pct"]
        baseline = risk_status.get("baseline_value") or 0
        pct_actual = (-risk_status["daily_pnl"] / baseline * 100) if baseline else 0
        col3.metric("Pérdida del día vs. límite", f"{pct_actual:.1f}% / {limite:.1f}%")
        st.caption(f"Última actualización: {risk_status.get('updated_at', '—')}")

# ============================== Paper trading ==============================
with tab_paper:
    st.subheader("📝 Paper trading — cartera virtual")
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

        precios_cache = {simbolo: precio_actual_para_dashboard(simbolo) for simbolo in positions}
        valor_posiciones = sum(
            p["cantidad"] * (precios_cache[simbolo] if precios_cache[simbolo] is not None else p["costo_promedio"])
            for simbolo, p in positions.items()
        )
        valorizado = cash + valor_posiciones
        pnl_total = valorizado - initial_cash
        pnl_total_pct = (pnl_total / initial_cash * 100) if initial_cash else 0

        # Ganancia del día: la persiste RiskManager en cada ciclo de scripts/paper_trade.py (mismo
        # mecanismo que el circuit breaker de la cuenta real) — compara contra el valor de la
        # cartera al comienzo del día, no contra el capital inicial de toda la sesión. Si el último
        # ciclo registrado es de un día anterior (ej. antes de que abra el mercado, o si el proceso
        # se cayó — ver logs/paper_trade_run.log), NO es el P&L de hoy todavía: mostrarlo como tal
        # sería engañoso, así que se marca aparte en vez de mezclarlo con datos frescos.
        paper_risk_status = load_status(PAPER_RISK_STATE_PATH)
        ganancia_diaria, ganancia_diaria_pct, dato_diario_de_otro_dia = None, None, False
        if paper_risk_status:
            actualizado_en = paper_risk_status.get("updated_at")
            es_de_hoy = False
            if actualizado_en:
                try:
                    es_de_hoy = datetime.fromisoformat(actualizado_en).date() == date.today()
                except ValueError:
                    es_de_hoy = False

            if es_de_hoy:
                ganancia_diaria = paper_risk_status.get("daily_pnl")
                baseline_dia = paper_risk_status.get("baseline_value") or 0
                if ganancia_diaria is not None and baseline_dia:
                    ganancia_diaria_pct = ganancia_diaria / baseline_dia * 100
            else:
                dato_diario_de_otro_dia = True

        st.write("**Composición de la cartera**")
        col1, col2, col3 = st.columns(3)
        col1.metric("Cartera virtual inicial", f"${initial_cash:,.2f}")
        col2.metric("Cash invertido (posiciones)", f"${valor_posiciones:,.2f}")
        col3.metric("Cash disponible", f"${cash:,.2f}")

        st.write("**Resultado**")
        col4, col5, col6 = st.columns(3)
        col4.metric("Valor total de la cartera", f"${valorizado:,.2f}")
        if ganancia_diaria is not None:
            col5.metric("Ganancia/pérdida HOY", f"${ganancia_diaria:,.2f}", delta=f"{ganancia_diaria_pct:.2f}%")
        elif dato_diario_de_otro_dia:
            col5.metric("Ganancia/pérdida HOY", "N/D")
            st.caption("⚠️ Todavía no corrió ningún ciclo hoy — el último dato guardado es de un día anterior.")
        else:
            col5.metric("Ganancia/pérdida HOY", "N/D")
        col6.metric("Ganancia/pérdida total", f"${pnl_total:,.2f}", delta=f"{pnl_total_pct:.2f}%")

        actualizaciones = f"Posiciones: {paper_status.get('updated_at', '—')}"
        if paper_risk_status:
            actualizaciones += f"  |  P&L diario: {paper_risk_status.get('updated_at', '—')}"
        st.caption(actualizaciones)

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
                            "valor total": None,
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
                        "valor total": round(p["cantidad"] * precio_actual, 2),
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

        paper_signals_df = read_csv_log(PAPER_SIGNALS_LOG, ["timestamp", "simbolo", "signal", "precio", "motivo", "estado"])
        if not paper_signals_df.empty:
            with st.expander(f"Ver las {len(paper_signals_df)} señales de paper trading (ejecutadas o no)"):
                st.dataframe(
                    paper_signals_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True
                )

# ============================== Ranking TOP 50 ==============================
with tab_ranking:
    st.subheader("🏆 Ranking TOP 50 — motor de scoring")
    st.caption(
        "Score 0-100 por símbolo, calculado con config/scoring.yaml a partir de momentum, tendencia, "
        "momentum técnico, volatilidad, volumen, fuerza relativa vs. benchmark y performance "
        "ajustada por riesgo. Puede tardar más que el resto del dashboard: calcula features para "
        "todo el pool de candidatos, no solo el TOP final."
    )
    try:
        scoring_cfg = ScoringConfig.load()
    except (OSError, ValueError) as exc:
        scoring_cfg = None
        st.error(f"No se pudo cargar config/scoring.yaml: {exc}")

    if scoring_cfg is not None:
        try:
            ranking_hoy, ranking_previo = fetch_ranking()
        except IOLApiError as exc:
            ranking_hoy, ranking_previo = None, None
            st.error(f"Error calculando el ranking: {exc}")

        if ranking_hoy is None or ranking_hoy.empty:
            st.info("Todavía no hay ranking calculado para hoy.")
        else:
            diff = diff_rankings(ranking_hoy, ranking_previo, top_n=scoring_cfg.top_n_final)
            col1, col2, col3 = st.columns(3)
            col1.metric("Pool elegible hoy", int(ranking_hoy["eligible"].sum()))
            col2.metric("Entradas al TOP vs. ranking anterior", len(diff["entries"]))
            col3.metric("Salidas del TOP vs. ranking anterior", len(diff["exits"]))
            if diff["entries"]:
                st.caption("Entraron: " + ", ".join(diff["entries"]))
            if diff["exits"]:
                st.caption("Salieron: " + ", ".join(diff["exits"]))

            group_score_cols = [f"{grupo}_score" for grupo in scoring_cfg.groups if f"{grupo}_score" in ranking_hoy.columns]
            columnas_top = ["rank", "simbolo", "ultimo_precio", "score_total"] + group_score_cols

            top_df = ranking_hoy[ranking_hoy["eligible"] & (ranking_hoy["rank"] <= scoring_cfg.top_n_final)]
            top_df = top_df.sort_values("rank")[columnas_top]
            st.dataframe(top_df.round(2), use_container_width=True, hide_index=True)

            resto_elegible = ranking_hoy[ranking_hoy["eligible"] & (ranking_hoy["rank"] > scoring_cfg.top_n_final)]
            if not resto_elegible.empty:
                with st.expander(f"Ver los {len(resto_elegible)} candidatos elegibles fuera del TOP {scoring_cfg.top_n_final}"):
                    st.dataframe(
                        resto_elegible.sort_values("rank")[columnas_top].round(2), use_container_width=True, hide_index=True
                    )

            excluidos = ranking_hoy[~ranking_hoy["eligible"]]
            if not excluidos.empty:
                with st.expander(f"Ver los {len(excluidos)} candidatos excluidos y el motivo"):
                    st.dataframe(
                        excluidos[["simbolo", "ultimo_precio", "volumen_nominal", "motivo"]],
                        use_container_width=True,
                        hide_index=True,
                    )

# ============================== Backtests ==============================
with tab_backtests:
    st.subheader("📈 Backtests")
    st.caption(
        "Corré `python -m scripts.run_backtest` para generar una corrida nueva (universo, rango de "
        "fechas y costos se ajustan en config/backtest.yaml). Cada corrida queda guardada en "
        "backtests/ — acá se puede revisar cualquiera de las que ya corriste."
    )

    corridas = _listar_backtests()
    if not corridas:
        st.info("Todavía no corriste ningún backtest.")
    else:
        run_elegido = st.selectbox("Corrida", corridas, help="Carpeta backtests/{fecha}_{hora}/")
        run_dir = BACKTESTS_DIR / run_elegido

        with open(run_dir / "summary.json", encoding="utf-8") as f:
            summary = json.load(f)

        cfg = summary["config"]
        met_e = summary["estrategia"]
        met_b = summary["benchmark"]
        simbolos_universo = ", ".join(s["simbolo"] for s in cfg["simbolos"])

        st.caption(
            f"**Símbolos:** {simbolos_universo}  |  **Período:** {cfg['fecha_desde']} a {cfg['fecha_hasta']}  |  "
            f"**Capital inicial:** ${cfg['capital_inicial']:,.0f}  |  **Benchmark:** {cfg['benchmark_simbolo']}"
        )

        def _delta(clave, sufijo=""):
            v_e, v_b = met_e.get(clave), met_b.get(clave)
            if v_e is None or v_b is None:
                return None
            return f"{v_e - v_b:+.2f}{sufijo} vs. benchmark"

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Retorno total", f"{met_e.get('retorno_total_pct', float('nan')):.2f}%", delta=_delta("retorno_total_pct", "pp"))
        col2.metric("Sharpe", f"{met_e.get('sharpe', float('nan')):.2f}", delta=_delta("sharpe"))
        col3.metric("Max drawdown", f"{met_e.get('max_drawdown_pct', float('nan')):.2f}%")
        col4.metric("Operaciones", f"{met_e.get('numero_operaciones', 0):.0f}")

        equity_path = run_dir / "equity_curve.csv"
        if equity_path.exists():
            equity_df = pd.read_csv(equity_path, parse_dates=["fecha"])
            fig = go.Figure()
            fig.add_trace(
                go.Scatter(x=equity_df["fecha"], y=equity_df["equity_estrategia"], name="Estrategia", line=dict(color=COLOR_ESTRATEGIA, width=2))
            )
            if "equity_benchmark" in equity_df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=equity_df["fecha"],
                        y=equity_df["equity_benchmark"],
                        name=f"Buy & hold {cfg['benchmark_simbolo']}",
                        line=dict(color=COLOR_BENCHMARK, width=2),
                    )
                )
            fig.update_layout(
                template="plotly_white",
                height=380,
                margin=dict(l=10, r=10, t=30, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                yaxis_title="Valor de la cartera (ARS)",
                hovermode="x unified",
            )
            st.plotly_chart(fig, use_container_width=True)

        with st.expander("Ver todas las métricas"):
            claves = sorted(set(met_e) | set(met_b))
            filas_metricas = [{"Métrica": k, "Estrategia": met_e.get(k), "Benchmark": met_b.get(k)} for k in claves]
            st.dataframe(pd.DataFrame(filas_metricas), use_container_width=True, hide_index=True)

        trades_path = run_dir / "trades.csv"
        if trades_path.exists():
            trades_df = pd.read_csv(trades_path)
            ventas = trades_df[trades_df["lado"] == "VENTA"] if not trades_df.empty and "lado" in trades_df.columns else pd.DataFrame()
            if not ventas.empty:
                st.write("**Resultado por símbolo**")
                resumen = (
                    ventas.groupby("simbolo")["pnl_realizado"]
                    .agg(operaciones="count", pnl_total="sum")
                    .reset_index()
                    .sort_values("pnl_total")
                )
                colores_barras = [COLOR_GOOD if v >= 0 else COLOR_CRITICAL for v in resumen["pnl_total"]]
                fig_pnl = go.Figure(
                    go.Bar(
                        x=resumen["pnl_total"],
                        y=resumen["simbolo"],
                        orientation="h",
                        marker_color=colores_barras,
                        text=[f"${v:,.0f}" for v in resumen["pnl_total"]],
                        textposition="outside",
                    )
                )
                fig_pnl.update_layout(
                    template="plotly_white",
                    height=max(220, 38 * len(resumen)),
                    margin=dict(l=10, r=10, t=20, b=10),
                    xaxis_title="P&L total realizado (ARS)",
                    showlegend=False,
                )
                st.plotly_chart(fig_pnl, use_container_width=True)

            with st.expander(f"Ver las {len(trades_df)} operaciones de esta corrida"):
                st.dataframe(trades_df, use_container_width=True, hide_index=True)

# ============================== Actividad (señales + operaciones) ==============================
with tab_actividad:
    st.subheader("Señales generadas (ejecutadas o no)")
    signals_df = read_csv_log(SIGNALS_LOG, ["timestamp", "simbolo", "signal", "precio", "motivo", "estado"])
    if signals_df.empty:
        st.info("Todavía no hay señales registradas — corré el bot o `scripts/smoke_test.py`.")
    else:
        filtro_simbolos = st.multiselect("Filtrar por símbolo", sorted(signals_df["simbolo"].unique()))
        df_mostrado = signals_df if not filtro_simbolos else signals_df[signals_df["simbolo"].isin(filtro_simbolos)]
        st.dataframe(df_mostrado.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Operaciones ejecutadas o simuladas")
    trades_df = read_csv_log(TRADES_LOG, ["timestamp", "modo", "simbolo", "lado", "cantidad", "precio", "motivo", "resultado"])
    if trades_df.empty:
        st.info("Todavía no hay operaciones registradas.")
    else:
        st.dataframe(trades_df.sort_values("timestamp", ascending=False), use_container_width=True, hide_index=True)

# ============================== Símbolo (precio + indicadores) ==============================
with tab_simbolo:
    st.subheader("Precio e indicadores por símbolo")
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
            sma_rapida = sma(price_df["cierre"], 20)
            sma_lenta = sma(price_df["cierre"], 50)
            rsi_series = rsi(price_df["cierre"], 14)

            fig = make_subplots(
                rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.06,
                subplot_titles=(f"{simbolo_elegido} — precio y medias móviles", "RSI (14)"),
            )
            fig.add_trace(go.Scatter(x=price_df["fecha"], y=price_df["cierre"], name="Precio", line=dict(color=COLOR_PRECIO, width=1.5)), row=1, col=1)
            fig.add_trace(go.Scatter(x=price_df["fecha"], y=sma_rapida.values, name="SMA 20", line=dict(color=COLOR_SMA_RAPIDA, width=2)), row=1, col=1)
            fig.add_trace(go.Scatter(x=price_df["fecha"], y=sma_lenta.values, name="SMA 50", line=dict(color=COLOR_SMA_LENTA, width=2)), row=1, col=1)
            fig.add_trace(
                go.Scatter(x=price_df["fecha"], y=rsi_series.values, name="RSI 14", line=dict(color=COLOR_RSI, width=2), showlegend=False),
                row=2, col=1,
            )
            fig.add_hline(y=70, line_dash="dot", line_color=COLOR_MUTED, row=2, col=1)
            fig.add_hline(y=30, line_dash="dot", line_color=COLOR_MUTED, row=2, col=1)
            fig.update_yaxes(title_text="Precio (ARS)", row=1, col=1)
            fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
            fig.update_layout(
                template="plotly_white",
                height=560,
                hovermode="x unified",
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.08, xanchor="right", x=1),
            )
            st.plotly_chart(fig, use_container_width=True)
            st.caption("RSI: sobrecompra ≥ 70, sobreventa ≤ 30 (líneas punteadas de referencia).")
    else:
        st.info("El scan de mercado no devolvió símbolos — revisá PANELES en tu .env.")
