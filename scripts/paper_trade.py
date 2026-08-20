"""Paper trading: corre la estrategia contra una cartera 100% virtual (arranca con
PAPER_INITIAL_CASH, default $100.000 ARS) usando precios reales de mercado de solo lectura.

Nunca llama a /api/operar/Comprar ni /api/operar/Vender — las compras/ventas se resuelven en
iol_bot/paper_portfolio.py, en memoria y en logs/paper_portfolio.json. Es completamente aparte de
main.py: no toca logs/trades.csv, logs/signals.csv ni logs/risk_state.json (esos son del bot real).

Corre ciclos hasta que cierra el mercado hoy y después imprime un resumen. Pensado para dejarlo
corriendo en background durante la sesión.

Uso: python -m scripts.paper_trade
"""
import csv
import logging
import time
from datetime import date, datetime

from iol_bot.auth import IOLAuth
from iol_bot.client import IOLApiError, IOLClient
from iol_bot.config import LOGS_DIR, Config
from iol_bot.logging_config import setup_logging
from iol_bot.main import _scores_del_ranking_actual, is_market_open, should_trade_now
from iol_bot.market_data import get_historical_prices_cached
from iol_bot.paper_portfolio import PaperPortfolio
from iol_bot.ranking import build_daily_ranking, get_current_ranking
from iol_bot.risk import RiskManager, apply_position_override, apply_rotation_cooldown, find_rotation_candidate
from iol_bot.strategy import Signal, SmaCrossoverRsiStrategy

logger = logging.getLogger("iol_bot.paper_trade")

PAPER_PORTFOLIO_PATH = LOGS_DIR / "paper_portfolio.json"
PAPER_RISK_STATE_PATH = LOGS_DIR / "paper_risk_state.json"
PAPER_TRADES_LOG = LOGS_DIR / "paper_trades.csv"
PAPER_SIGNALS_LOG = LOGS_DIR / "paper_signals.csv"
PAPER_EQUITY_LOG = LOGS_DIR / "paper_equity.csv"

REQUEST_DELAY_SECONDS = 0.3


def _log_csv(path, header, row):
    LOGS_DIR.mkdir(exist_ok=True)
    is_new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(header)
        writer.writerow(row)


def _log_paper_trade(simbolo, lado, cantidad, precio, motivo, resultado):
    _log_csv(
        PAPER_TRADES_LOG,
        ["timestamp", "simbolo", "lado", "cantidad", "precio", "motivo", "resultado"],
        [datetime.now().isoformat(), simbolo, lado, cantidad, precio, motivo, resultado],
    )


def _log_paper_signal(trade_signal, estado):
    _log_csv(
        PAPER_SIGNALS_LOG,
        ["timestamp", "simbolo", "signal", "precio", "motivo", "estado"],
        [
            datetime.now().isoformat(),
            trade_signal.simbolo,
            trade_signal.signal.value,
            trade_signal.precio,
            trade_signal.motivo,
            estado,
        ],
    )


def _log_paper_equity(status):
    """Un renglón por ciclo con el valorizado total -- es la única fuente que tiene el dashboard
    para dibujar la curva de equity del paper trading en vivo (a diferencia de los backtests, acá
    no existe de antes un historial de valorizado por día, así que arranca a juntarse desde que se
    agrega este log, no retroactivo)."""
    _log_csv(
        PAPER_EQUITY_LOG,
        ["timestamp", "valorizado_total", "cash", "pnl_total", "pnl_total_pct"],
        [
            datetime.now().isoformat(),
            status["valorizado_total"],
            status["cash"],
            status["pnl_total"],
            status["pnl_total_pct"],
        ],
    )


def _intentar_rotacion(risk_manager, portfolio, simbolo_candidato, precios_actuales, scores_por_simbolo, cooldown_rotacion):
    """Análogo a iol_bot.main._intentar_rotacion, pero para la cartera virtual (PaperPortfolio).
    Devuelve True si vendió algo (el llamador debe recalcular portfolio_value/exposición antes de
    reintentar el size_buy_order del candidato)."""
    simbolo_a_vender = find_rotation_candidate(
        portfolio.positions, simbolo_candidato, precios_actuales, scores_por_simbolo, risk_manager.limits.min_mejora_score_rotacion
    )
    if not simbolo_a_vender:
        return False

    precio_venta = precios_actuales.get(simbolo_a_vender)
    cantidad_venta = portfolio.positions[simbolo_a_vender]["cantidad"]
    if not precio_venta or cantidad_venta <= 0:
        return False

    ok, pnl = portfolio.sell(simbolo_a_vender, cantidad_venta, precio_venta)
    if ok:
        _log_paper_trade(
            simbolo_a_vender, "VENTA", cantidad_venta, precio_venta,
            f"rotación: liberar lugar para {simbolo_candidato} (score mejor)", f"pnl_realizado={pnl:.2f}",
        )
        logger.info(
            "[PAPER] VENTA (rotación) %s x%s @ %.2f para liberar lugar a %s",
            simbolo_a_vender, cantidad_venta, precio_venta, simbolo_candidato,
        )
        # Ver el comentario de RiskLimits.rotacion_cooldown_minutos (iol_bot/config.py) -- evita
        # recomprarlo enseguida si su propia señal técnica sigue en BUY apenas se libera cash.
        cooldown_rotacion[simbolo_a_vender] = datetime.now()
    return ok


def run_cycle(client, strategy, risk_manager, portfolio, watchlist, cooldown_rotacion=None):
    cooldown_rotacion = {} if cooldown_rotacion is None else cooldown_rotacion
    precios_actuales = {}

    # Marcar a mercado TODAS las posiciones abiertas antes de medir la cartera del ciclo — no solo
    # las que quedaron fuera del watchlist. Antes, una posición que SÍ seguía en el watchlist recién
    # conseguía precio fresco más adelante en este mismo ciclo (dentro del loop de abajo), así que
    # el portfolio_value que ve el circuit breaker/daily P&L la medía todavía a costo de compra —
    # resultado: "ganancia de hoy" podía mostrar un número inconsistente con el valorizado total
    # real de la cartera (bug real detectado el 2026-08-13 comparando ambos en el dashboard).
    for simbolo in list(portfolio.positions):
        try:
            cot = client.cotizacion(simbolo)
            precios_actuales[simbolo] = cot.get("ultimoPrecio", portfolio.positions[simbolo]["costo_promedio"])
        except IOLApiError:
            pass

    portfolio_value = portfolio.valorizado_total(precios_actuales)
    risk_manager.update_portfolio_value(portfolio_value)

    # Rotación de posiciones (ver iol_bot/config.py — apagada por defecto).
    scores_por_simbolo = _scores_del_ranking_actual() if risk_manager.limits.rotacion_habilitada else {}

    for i, item in enumerate(watchlist):
        simbolo, mercado = item["simbolo"], item["mercado"]
        try:
            price_df = get_historical_prices_cached(client, simbolo, mercado=mercado, hoy_precio=item.get("ultimo_precio"))
            if price_df.empty:
                continue

            precio_actual = float(price_df["cierre"].iloc[-1])
            precios_actuales[simbolo] = precio_actual
            portfolio_value = portfolio.valorizado_total(precios_actuales)

            trade_signal = strategy.evaluate(simbolo, price_df)
            posicion = portfolio.positions.get(simbolo, {"cantidad": 0, "costo_promedio": 0.0})
            trade_signal = apply_position_override(
                trade_signal, posicion["cantidad"], posicion.get("costo_promedio"), risk_manager.limits
            )
            trade_signal = apply_rotation_cooldown(
                trade_signal, cooldown_rotacion, risk_manager.limits.rotacion_cooldown_minutos
            )
            exposicion_actual = portfolio.exposicion(simbolo, precio_actual)
            exposicion_total_actual = portfolio_value - portfolio.cash

            estado = "hold"
            if trade_signal.signal == Signal.BUY:
                cantidad, motivo = risk_manager.size_buy_order(
                    precio_actual, portfolio_value, exposicion_actual, exposicion_total_actual
                )

                if cantidad <= 0 and scores_por_simbolo:
                    if _intentar_rotacion(risk_manager, portfolio, simbolo, precios_actuales, scores_por_simbolo, cooldown_rotacion):
                        portfolio_value = portfolio.valorizado_total(precios_actuales)
                        exposicion_total_actual = portfolio_value - portfolio.cash
                        cantidad, motivo = risk_manager.size_buy_order(
                            precio_actual, portfolio_value, exposicion_actual, exposicion_total_actual
                        )

                if cantidad > 0 and portfolio.buy(simbolo, cantidad, precio_actual):
                    estado = "ejecutada"
                    _log_paper_trade(simbolo, "COMPRA", cantidad, precio_actual, trade_signal.motivo, "ok")
                    logger.info("[PAPER] COMPRA %s x%s @ %.2f", simbolo, cantidad, precio_actual)
                else:
                    estado = "rechazada_por_riesgo"

            elif trade_signal.signal == Signal.SELL:
                cantidad_disponible = posicion["cantidad"]
                if cantidad_disponible > 0:
                    aprobado, _motivo = risk_manager.approve_sell()
                    if aprobado:
                        ok, pnl = portfolio.sell(simbolo, cantidad_disponible, precio_actual)
                        if ok:
                            estado = "ejecutada"
                            _log_paper_trade(
                                simbolo, "VENTA", cantidad_disponible, precio_actual, trade_signal.motivo,
                                f"pnl_realizado={pnl:.2f}",
                            )
                            logger.info(
                                "[PAPER] VENTA %s x%s @ %.2f (pnl_realizado=%.2f)",
                                simbolo, cantidad_disponible, precio_actual, pnl,
                            )
                    else:
                        estado = "rechazada_por_riesgo"

            _log_paper_signal(trade_signal, estado)
        except IOLApiError as exc:
            logger.error("Error de API procesando %s, se lo salta: %s", simbolo, exc)
        except Exception:
            logger.exception("Error inesperado procesando %s", simbolo)

        if i < len(watchlist) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    status = portfolio.status(precios_actuales)
    _log_paper_equity(status)
    return status


def _mark_to_market_final(client, portfolio):
    """Último precio real de cada posición abierta, para que el resumen final refleje la
    ganancia/pérdida NO realizada de verdad (mark-to-market), no el costo de compra."""
    precios = {}
    for simbolo in portfolio.positions:
        try:
            cot = client.cotizacion(simbolo)
            precios[simbolo] = cot.get("ultimoPrecio", portfolio.positions[simbolo]["costo_promedio"])
        except IOLApiError as exc:
            logger.warning("No se pudo traer el último precio de %s para el resumen final: %s", simbolo, exc)
    return precios


def main():
    setup_logging()
    config = Config.load()

    auth = IOLAuth(config.base_url, config.username, config.password)
    client = IOLClient(config.base_url, auth)
    strategy = SmaCrossoverRsiStrategy()
    risk_manager = RiskManager(config.risk, state_path=PAPER_RISK_STATE_PATH)
    portfolio = PaperPortfolio(PAPER_PORTFOLIO_PATH, initial_cash=config.paper_initial_cash)
    # Vive mientras dure el proceso (no se persiste a disco): un reinicio ya implica un "reset"
    # natural del cooldown, y no vale la pena la complejidad de un estado nuevo en logs/ para eso.
    cooldown_rotacion = {}

    logger.info(
        "Paper trading iniciado | cash=$%.2f paneles=%s max_simbolos=%s intervalo=%smin",
        portfolio.cash, config.paneles, config.max_simbolos_a_analizar, config.loop_interval_minutes,
    )

    if not is_market_open():
        logger.warning("Mercado cerrado ahora mismo — no hay ciclos para correr hoy. Salgo sin hacer nada.")
        return

    current_day = date.today()
    while is_market_open():
        if date.today() != current_day:
            risk_manager.reset_daily()
            current_day = date.today()

        if not should_trade_now(config):
            logger.info(
                "Mercado abierto pero todavía no llegó el horario de arranque configurado (%s) -- esperando",
                config.trading_start_time,
            )
            time.sleep(config.loop_interval_minutes * 60)
            continue

        try:
            watchlist = build_daily_ranking(client, config, held_symbols=set(portfolio.positions))
            status = run_cycle(client, strategy, risk_manager, portfolio, watchlist, cooldown_rotacion=cooldown_rotacion)
            logger.info(
                "Ciclo completo | cash=$%.2f valorizado=$%.2f pnl=$%.2f (%.2f%%) posiciones=%s",
                portfolio.cash, status["valorizado_total"], status["pnl_total"], status["pnl_total_pct"],
                list(portfolio.positions.keys()),
            )
        except IOLApiError as exc:
            logger.error("Error de API en este ciclo, se lo salta y se reintenta en el próximo: %s", exc)
        except Exception:
            logger.exception("Error inesperado en este ciclo, se lo salta y se reintenta en el próximo")

        time.sleep(config.loop_interval_minutes * 60)

    precios_cierre = _mark_to_market_final(client, portfolio)
    status_final = portfolio.status(precios_cierre)

    logger.info("Mercado cerrado — fin de la sesión de paper trading de hoy.")
    print("\n=== Resumen paper trading — cierre del mercado ===")
    print(f"Cartera inicial: ${status_final['initial_cash']:,.2f}")
    print(f"Cash final: ${portfolio.cash:,.2f}")
    if portfolio.positions:
        print("Posiciones abiertas (marcadas al último precio):")
        for simbolo, pos in portfolio.positions.items():
            precio_actual = precios_cierre.get(simbolo, pos["costo_promedio"])
            ganancia_pct = (precio_actual - pos["costo_promedio"]) / pos["costo_promedio"] * 100 if pos["costo_promedio"] else 0
            print(
                f"  {simbolo}: {pos['cantidad']} u. @ costo {pos['costo_promedio']:.2f}, "
                f"último {precio_actual:.2f} ({ganancia_pct:+.2f}%)"
            )
    else:
        print("Posiciones abiertas: (ninguna)")
    print(f"Valorizado total: ${status_final['valorizado_total']:,.2f}")
    print(f"P&L total (incluye no realizado): ${status_final['pnl_total']:,.2f} ({status_final['pnl_total_pct']:.2f}%)")
    print(f"Detalle en {PAPER_TRADES_LOG} y {PAPER_SIGNALS_LOG}")


if __name__ == "__main__":
    main()
