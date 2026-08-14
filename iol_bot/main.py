import csv
import logging
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from iol_bot.auth import IOLAuth
from iol_bot.client import IOLApiError, IOLClient
from iol_bot.config import LOGS_DIR, Config
from iol_bot.executor import OrderExecutor
from iol_bot.logging_config import setup_logging
from iol_bot.market_data import get_historical_prices_cached
from iol_bot.ranking import build_daily_ranking, get_current_ranking
from iol_bot.risk import RiskManager, apply_position_override, find_rotation_candidate
from iol_bot.signals_log import estado_from_execution_result, log_signal
from iol_bot.strategy import Signal, SmaCrossoverRsiStrategy, TradeSignal

RISK_STATE_PATH = LOGS_DIR / "risk_state.json"
EQUITY_LOG = LOGS_DIR / "equity.csv"

logger = logging.getLogger("iol_bot.main")

BYMA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
BYMA_OPEN_HOUR = 11
BYMA_CLOSE_HOUR = 17

# Pausa entre símbolos al procesar el ciclo: con hasta MAX_SIMBOLOS_A_ANALIZAR (default 50)
# llamadas de serie histórica por ciclo, conviene no ráfaguear la API.
REQUEST_DELAY_SECONDS = 0.3


def is_market_open(now=None):
    now = now or datetime.now(BYMA_TZ)
    if now.weekday() >= 5:  # sábado/domingo
        return False
    return BYMA_OPEN_HOUR <= now.hour < BYMA_CLOSE_HOUR


def build_posiciones_por_simbolo(portafolio):
    posiciones = {}
    for activo in portafolio.get("activos", []):
        simbolo = activo["titulo"]["simbolo"]
        posiciones[simbolo] = {
            "cantidad": activo.get("cantidad", 0),
            "valorizado": activo.get("valorizado", 0.0),
            "ppc": activo.get("ppc", 0.0),
        }
    return posiciones


def _log_equity(portfolio_value):
    """Un renglón por ciclo con el valor total de la cuenta REAL -- misma idea que
    scripts/paper_trade.py::_log_paper_equity, para que la pestaña "Resumen" del dashboard pueda
    dibujar la curva de equity de la cuenta real, no solo de paper trading. Arranca a juntarse
    desde que se agrega este log, no es retroactivo."""
    LOGS_DIR.mkdir(exist_ok=True)
    is_new = not EQUITY_LOG.exists()
    with open(EQUITY_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "valorizado_total"])
        writer.writerow([datetime.now().isoformat(), portfolio_value])


def _scores_del_ranking_actual():
    ranking_hoy = get_current_ranking()
    if ranking_hoy is None or ranking_hoy.empty or "score_total" not in ranking_hoy.columns:
        return {}
    # dropna: un score_total NaN (ej. una posición en cartera con historia de precios insuficiente
    # para calcular features) rompe la comparación de find_rotation_candidate en iol_bot/risk.py —
    # cualquier comparación con NaN da False en Python, así que si un símbolo NaN queda como "peor
    # score visto", ningún candidato real puede reemplazarlo después. Tratarlo como ausente
    # (mismo comportamiento que si el símbolo no estuviera en el ranking) es lo seguro.
    validos = ranking_hoy.dropna(subset=["score_total"])
    return dict(zip(validos["simbolo"], validos["score_total"]))


def _intentar_rotacion(executor, risk_manager, simbolo_candidato, posiciones, portfolio_value, exposicion_total_actual, scores_por_simbolo):
    """Si `simbolo_candidato` no consiguió margen para comprar, busca la posición más débil (peor
    score, sin pérdida) y la vende para liberarle lugar. Devuelve la nueva exposicion_total_actual
    (sin cambios si no se pudo/quiso rotar)."""
    precios_posiciones = {
        s: (p["valorizado"] / p["cantidad"]) if p.get("cantidad") else None for s, p in posiciones.items()
    }
    simbolo_a_vender = find_rotation_candidate(
        posiciones, simbolo_candidato, precios_posiciones, scores_por_simbolo, risk_manager.limits.min_mejora_score_rotacion
    )
    if not simbolo_a_vender:
        return exposicion_total_actual

    pos_vender = posiciones[simbolo_a_vender]
    precio_vender = precios_posiciones[simbolo_a_vender]
    venta_signal = TradeSignal(
        simbolo_a_vender, Signal.SELL, precio_vender, f"rotación: liberar lugar para {simbolo_candidato} (score mejor)"
    )
    result = executor.handle_signal(venta_signal, portfolio_value, pos_vender, exposicion_total_actual)
    log_signal(venta_signal, estado_from_execution_result(venta_signal, result))

    if result and result.get("ok"):
        exposicion_total_actual -= pos_vender.get("valorizado", 0.0)
        del posiciones[simbolo_a_vender]

    return exposicion_total_actual


def run_cycle(client, strategy, executor, risk_manager, watchlist):
    try:
        estado_cuenta = client.estado_cuenta()
        portafolio = client.portafolio()
    except IOLApiError as exc:
        logger.error("No se pudo leer cuenta/portafolio, se salta este ciclo: %s", exc)
        return

    portfolio_value = float(estado_cuenta.get("totalEnPesos", 0.0))
    posiciones = build_posiciones_por_simbolo(portafolio)
    # Snapshot de exposición total al inicio del ciclo (suma de todas las posiciones ya abiertas).
    # No se actualiza símbolo a símbolo dentro del mismo ciclo — misma precisión que portfolio_value
    # y posiciones acá arriba, que también vienen de una única lectura de portafolio()/estadocuenta().
    exposicion_total_actual = sum(p["valorizado"] for p in posiciones.values())
    risk_manager.update_portfolio_value(portfolio_value)
    _log_equity(portfolio_value)

    # Rotación de posiciones (ver iol_bot/config.py — apagada por defecto): solo se calcula el
    # score del día si está habilitada, para no gastar de más en el caso común.
    scores_por_simbolo = _scores_del_ranking_actual() if risk_manager.limits.rotacion_habilitada else {}

    for i, item in enumerate(watchlist):
        simbolo, mercado = item["simbolo"], item["mercado"]
        try:
            price_df = get_historical_prices_cached(client, simbolo, mercado=mercado, hoy_precio=item.get("ultimo_precio"))
            trade_signal = strategy.evaluate(simbolo, price_df)
            posicion_actual = posiciones.get(simbolo, {"cantidad": 0, "valorizado": 0.0, "ppc": 0.0})
            trade_signal = apply_position_override(
                trade_signal, posicion_actual["cantidad"], posicion_actual.get("ppc"), risk_manager.limits
            )

            if trade_signal.signal == Signal.BUY and scores_por_simbolo:
                cantidad_posible, _motivo = risk_manager.size_buy_order(
                    trade_signal.precio, portfolio_value, posicion_actual.get("valorizado", 0.0), exposicion_total_actual
                )
                if cantidad_posible <= 0:
                    exposicion_total_actual = _intentar_rotacion(
                        executor, risk_manager, simbolo, posiciones, portfolio_value, exposicion_total_actual, scores_por_simbolo
                    )

            result = executor.handle_signal(trade_signal, portfolio_value, posicion_actual, exposicion_total_actual)
            log_signal(trade_signal, estado_from_execution_result(trade_signal, result))
        except IOLApiError as exc:
            logger.error("Error de API procesando %s, se lo salta: %s", simbolo, exc)
        except Exception:
            logger.exception("Error inesperado procesando %s", simbolo)

        if i < len(watchlist) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)


def main():
    setup_logging()
    config = Config.load()

    logger.info(
        "Iniciando iol_bot | dry_run_efectivo=%s paneles=%s max_simbolos=%s",
        config.effective_dry_run,
        config.paneles,
        config.max_simbolos_a_analizar,
    )
    if not config.effective_dry_run:
        logger.warning(
            "MODO LIVE: las órdenes se enviarán con DINERO REAL a tu cuenta de IOL. "
            "No existe entorno de pruebas separado para esta API."
        )
    elif not config.dry_run and config.confirm_live_trading is False:
        logger.warning(
            "DRY_RUN=false pero CONFIRM_LIVE_TRADING no está en true: por seguridad, el bot sigue "
            "corriendo en modo simulado. Ver README para pasar a real."
        )

    auth = IOLAuth(config.base_url, config.username, config.password)
    client = IOLClient(config.base_url, auth)
    strategy = SmaCrossoverRsiStrategy()
    risk_manager = RiskManager(config.risk, state_path=RISK_STATE_PATH)
    executor = OrderExecutor(client, risk_manager, dry_run=config.effective_dry_run)

    current_day = date.today()

    while True:
        if date.today() != current_day:
            risk_manager.reset_daily()
            current_day = date.today()

        if is_market_open():
            try:
                held_symbols = set()
                try:
                    held_symbols = set(build_posiciones_por_simbolo(client.portafolio()))
                except IOLApiError:
                    logger.warning("No se pudo leer el portafolio para incluir posiciones abiertas en el ranking de hoy")
                # Se recalcula el ranking cada ciclo (motor de scoring de iol_bot/ranking.py) para
                # que refleje el volumen/precio operado hasta ese momento del día, no una foto vieja.
                watchlist = build_daily_ranking(client, config, held_symbols=held_symbols)
                run_cycle(client, strategy, executor, risk_manager, watchlist)
            except IOLApiError as exc:
                logger.error("Error de API en este ciclo, se lo salta y se reintenta en el próximo: %s", exc)
            except Exception:
                logger.exception("Error inesperado en este ciclo, se lo salta y se reintenta en el próximo")
        else:
            logger.info("Mercado cerrado, esperando...")

        time.sleep(config.loop_interval_minutes * 60)


if __name__ == "__main__":
    main()
