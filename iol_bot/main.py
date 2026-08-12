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
from iol_bot.market_scanner import scan_market
from iol_bot.risk import RiskManager, apply_position_override
from iol_bot.signals_log import estado_from_execution_result, log_signal
from iol_bot.strategy import SmaCrossoverRsiStrategy

RISK_STATE_PATH = LOGS_DIR / "risk_state.json"

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


def run_cycle(client, strategy, executor, risk_manager, watchlist):
    try:
        estado_cuenta = client.estado_cuenta()
        portafolio = client.portafolio()
    except IOLApiError as exc:
        logger.error("No se pudo leer cuenta/portafolio, se salta este ciclo: %s", exc)
        return

    portfolio_value = float(estado_cuenta.get("totalEnPesos", 0.0))
    posiciones = build_posiciones_por_simbolo(portafolio)
    risk_manager.update_portfolio_value(portfolio_value)

    for i, item in enumerate(watchlist):
        simbolo, mercado = item["simbolo"], item["mercado"]
        try:
            price_df = get_historical_prices_cached(client, simbolo, mercado=mercado, hoy_precio=item.get("ultimo_precio"))
            trade_signal = strategy.evaluate(simbolo, price_df)
            posicion_actual = posiciones.get(simbolo, {"cantidad": 0, "valorizado": 0.0, "ppc": 0.0})
            trade_signal = apply_position_override(
                trade_signal, posicion_actual["cantidad"], posicion_actual.get("ppc"), risk_manager.limits
            )
            result = executor.handle_signal(trade_signal, portfolio_value, posicion_actual)
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
            # Se re-escanea cada ciclo (3 llamadas, barato) para que el ranking de liquidez
            # refleje el volumen operado hasta ese momento del día, no una foto vieja.
            watchlist = scan_market(client, paneles=config.paneles, top_n=config.max_simbolos_a_analizar)
            run_cycle(client, strategy, executor, risk_manager, watchlist)
        else:
            logger.info("Mercado cerrado, esperando...")

        time.sleep(config.loop_interval_minutes * 60)


if __name__ == "__main__":
    main()
