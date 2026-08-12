import csv
from datetime import datetime

from iol_bot.config import LOGS_DIR

SIGNALS_LOG = LOGS_DIR / "signals.csv"
_CSV_HEADER = ["timestamp", "simbolo", "signal", "precio", "motivo", "estado"]


def log_signal(trade_signal, estado):
    """Registra toda señal que la estrategia genera, se haya ejecutado o no.

    estado: "hold" | "simulada" | "ejecutada" | "rechazada_por_riesgo" | "rechazada_por_api"
    """
    LOGS_DIR.mkdir(exist_ok=True)
    is_new = not SIGNALS_LOG.exists()
    with open(SIGNALS_LOG, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(_CSV_HEADER)
        writer.writerow(
            [
                datetime.now().isoformat(),
                trade_signal.simbolo,
                trade_signal.signal.value,
                trade_signal.precio,
                trade_signal.motivo,
                estado,
            ]
        )


def estado_from_execution_result(trade_signal, result):
    from iol_bot.strategy import Signal

    if trade_signal.signal == Signal.HOLD:
        return "hold"
    if result is None:
        return "rechazada_por_riesgo"
    if result.get("simulado"):
        return "simulada"
    if result.get("ok"):
        return "ejecutada"
    return "rechazada_por_api"
