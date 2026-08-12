import csv

import pytest

import iol_bot.signals_log as signals_log_module
from iol_bot.signals_log import estado_from_execution_result, log_signal
from iol_bot.strategy import Signal, TradeSignal


@pytest.fixture(autouse=True)
def isolated_signals_log(tmp_path, monkeypatch):
    log_path = tmp_path / "signals.csv"
    monkeypatch.setattr(signals_log_module, "SIGNALS_LOG", log_path)
    monkeypatch.setattr(signals_log_module, "LOGS_DIR", tmp_path)
    return log_path


def _read_rows(log_path):
    with open(log_path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_log_signal_writes_header_and_row(isolated_signals_log):
    signal = TradeSignal("GGAL", Signal.BUY, precio=100.0, motivo="cruce alcista")
    log_signal(signal, "ejecutada")

    rows = _read_rows(isolated_signals_log)
    assert rows[0] == ["timestamp", "simbolo", "signal", "precio", "motivo", "estado"]
    assert rows[1][1:] == ["GGAL", "BUY", "100.0", "cruce alcista", "ejecutada"]


def test_log_signal_appends_without_duplicating_header(isolated_signals_log):
    signal = TradeSignal("GGAL", Signal.HOLD, precio=100.0, motivo="sin señal")
    log_signal(signal, "hold")
    log_signal(signal, "hold")

    rows = _read_rows(isolated_signals_log)
    assert len(rows) == 3  # header + 2 filas
    assert rows.count(["timestamp", "simbolo", "signal", "precio", "motivo", "estado"]) == 1


@pytest.mark.parametrize(
    "signal_type,result,expected",
    [
        (Signal.HOLD, None, "hold"),
        (Signal.BUY, None, "rechazada_por_riesgo"),
        (Signal.BUY, {"ok": True, "simulado": True}, "simulada"),
        (Signal.BUY, {"ok": True, "messages": []}, "ejecutada"),
        (Signal.BUY, {"ok": False, "messages": [{"description": "fondos insuficientes"}]}, "rechazada_por_api"),
        (Signal.SELL, None, "rechazada_por_riesgo"),
    ],
)
def test_estado_from_execution_result(signal_type, result, expected):
    signal = TradeSignal("GGAL", signal_type, precio=100.0, motivo="test")
    assert estado_from_execution_result(signal, result) == expected
