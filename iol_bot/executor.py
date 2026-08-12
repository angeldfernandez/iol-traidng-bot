import csv
import logging
from datetime import datetime, timedelta

from iol_bot.config import LOGS_DIR
from iol_bot.strategy import Signal

logger = logging.getLogger("iol_bot.executor")

TRADES_LOG = LOGS_DIR / "trades.csv"
_CSV_HEADER = ["timestamp", "modo", "simbolo", "lado", "cantidad", "precio", "motivo", "resultado"]


def _log_trade(row):
    LOGS_DIR.mkdir(exist_ok=True)
    is_new = not TRADES_LOG.exists()
    with open(TRADES_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(_CSV_HEADER)
        writer.writerow(row)


class OrderExecutor:
    def __init__(self, client, risk_manager, dry_run, mercado="bCBA", plazo="t0", validez_dias=1):
        self.client = client
        self.risk_manager = risk_manager
        self.dry_run = dry_run
        self.mercado = mercado
        self.plazo = plazo
        self.validez_dias = validez_dias

    def _validez(self):
        return (datetime.now() + timedelta(days=self.validez_dias)).strftime("%Y-%m-%d")

    def handle_signal(self, trade_signal, portfolio_value, posicion_actual):
        """posicion_actual: dict con 'cantidad' y 'valorizado' (valor de mercado actual) del símbolo."""
        if trade_signal.signal == Signal.HOLD:
            return None

        if trade_signal.signal == Signal.BUY:
            return self._handle_buy(trade_signal, portfolio_value, posicion_actual)

        return self._handle_sell(trade_signal, posicion_actual)

    def _handle_buy(self, trade_signal, portfolio_value, posicion_actual):
        exposicion_actual = posicion_actual.get("valorizado", 0.0)
        cantidad, motivo = self.risk_manager.size_buy_order(
            trade_signal.precio, portfolio_value, exposicion_actual
        )
        if cantidad <= 0:
            logger.info("BUY %s omitida: %s", trade_signal.simbolo, motivo)
            return None

        return self._place_order("COMPRA", trade_signal, cantidad, f"{trade_signal.motivo} | {motivo}")

    def _handle_sell(self, trade_signal, posicion_actual):
        cantidad_disponible = int(posicion_actual.get("cantidad", 0))
        if cantidad_disponible <= 0:
            logger.info("SELL %s omitida: no hay posición para vender", trade_signal.simbolo)
            return None

        aprobado, motivo = self.risk_manager.approve_sell()
        if not aprobado:
            logger.info("SELL %s omitida: %s", trade_signal.simbolo, motivo)
            return None

        return self._place_order("VENTA", trade_signal, cantidad_disponible, trade_signal.motivo)

    def _place_order(self, lado, trade_signal, cantidad, motivo):
        modo = "DRY_RUN" if self.dry_run else "LIVE"

        if self.dry_run:
            logger.info(
                "[DRY_RUN] %s %s x%s @ %.2f — %s", lado, trade_signal.simbolo, cantidad, trade_signal.precio, motivo
            )
            _log_trade(
                [
                    datetime.now().isoformat(),
                    modo,
                    trade_signal.simbolo,
                    lado,
                    cantidad,
                    trade_signal.precio,
                    motivo,
                    "simulado",
                ]
            )
            return {"ok": True, "simulado": True}

        validez = self._validez()
        try:
            if lado == "COMPRA":
                result = self.client.comprar(
                    trade_signal.simbolo, cantidad, trade_signal.precio, validez, mercado=self.mercado, plazo=self.plazo
                )
            else:
                result = self.client.vender(
                    trade_signal.simbolo, cantidad, trade_signal.precio, validez, mercado=self.mercado, plazo=self.plazo
                )
        except Exception as exc:
            logger.error("Error enviando orden %s %s: %s", lado, trade_signal.simbolo, exc)
            _log_trade(
                [datetime.now().isoformat(), modo, trade_signal.simbolo, lado, cantidad, trade_signal.precio, motivo, f"error: {exc}"]
            )
            raise

        resultado = "ok" if result.get("ok") else f"rechazada: {result.get('messages')}"
        logger.info("%s %s x%s @ %.2f — %s", lado, trade_signal.simbolo, cantidad, trade_signal.precio, resultado)
        _log_trade(
            [datetime.now().isoformat(), modo, trade_signal.simbolo, lado, cantidad, trade_signal.precio, motivo, resultado]
        )
        return result
