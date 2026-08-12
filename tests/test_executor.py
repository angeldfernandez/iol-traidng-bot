import csv

import pytest

import iol_bot.executor as executor_module
from iol_bot.config import RiskLimits
from iol_bot.executor import OrderExecutor
from iol_bot.risk import RiskManager
from iol_bot.strategy import Signal, TradeSignal


class FakeClient:
    def __init__(self, buy_result=None, sell_result=None):
        self.buy_calls = []
        self.sell_calls = []
        self._buy_result = buy_result or {"ok": True, "messages": []}
        self._sell_result = sell_result or {"ok": True, "messages": []}

    def comprar(self, simbolo, cantidad, precio, validez, mercado="bCBA", plazo="t0"):
        self.buy_calls.append((simbolo, cantidad, precio))
        return self._buy_result

    def vender(self, simbolo, cantidad, precio, validez, mercado="bCBA", plazo="t0"):
        self.sell_calls.append((simbolo, cantidad, precio))
        return self._sell_result


@pytest.fixture(autouse=True)
def isolated_trades_log(tmp_path, monkeypatch):
    log_path = tmp_path / "trades.csv"
    monkeypatch.setattr(executor_module, "TRADES_LOG", log_path)
    monkeypatch.setattr(executor_module, "LOGS_DIR", tmp_path)
    return log_path


def _risk_manager(**overrides):
    defaults = dict(
        max_monto_por_orden_pct=50,  # 50% de portfolio_value=100_000 = $50.000, igual que antes
        max_exposicion_por_simbolo_pct=50,
        max_perdida_diaria_pct=5,
        take_profit_pct=8,
        stop_loss_pct=5,
    )
    defaults.update(overrides)
    return RiskManager(RiskLimits(**defaults))


def _read_log_rows(log_path):
    with open(log_path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_dry_run_never_calls_real_client(isolated_trades_log):
    client = FakeClient()
    executor = OrderExecutor(client, _risk_manager(), dry_run=True)
    signal = TradeSignal("GGAL", Signal.BUY, precio=100.0, motivo="test")

    executor.handle_signal(signal, portfolio_value=100_000, posicion_actual={"cantidad": 0, "valorizado": 0})

    assert client.buy_calls == []
    rows = _read_log_rows(isolated_trades_log)
    assert rows[1][1] == "DRY_RUN"
    assert rows[1][3] == "COMPRA"


def test_live_buy_calls_client_when_risk_allows(isolated_trades_log):
    client = FakeClient()
    executor = OrderExecutor(client, _risk_manager(), dry_run=False)
    signal = TradeSignal("GGAL", Signal.BUY, precio=100.0, motivo="test")

    executor.handle_signal(signal, portfolio_value=100_000, posicion_actual={"cantidad": 0, "valorizado": 0})

    assert len(client.buy_calls) == 1
    simbolo, cantidad, precio = client.buy_calls[0]
    assert simbolo == "GGAL"
    assert cantidad > 0
    assert precio == 100.0


def test_live_buy_skipped_when_exposicion_total_ya_llena(isolated_trades_log):
    client = FakeClient()
    rm = _risk_manager(max_exposicion_total_pct=80)  # 80% de 100_000 = 80_000 de tope
    executor = OrderExecutor(client, rm, dry_run=False)
    signal = TradeSignal("GGAL", Signal.BUY, precio=100.0, motivo="test")

    executor.handle_signal(
        signal, portfolio_value=100_000, posicion_actual={"cantidad": 0, "valorizado": 0}, exposicion_total_actual=80_000
    )

    assert client.buy_calls == []
    assert not isolated_trades_log.exists()


def test_live_buy_skipped_when_risk_denies(isolated_trades_log):
    client = FakeClient()
    # 0.05% de portfolio_value=100_000 = $50, monto insuficiente para comprar 1 unidad a 100
    rm = _risk_manager(max_monto_por_orden_pct=0.05)
    executor = OrderExecutor(client, rm, dry_run=False)
    signal = TradeSignal("GGAL", Signal.BUY, precio=100.0, motivo="test")

    executor.handle_signal(signal, portfolio_value=100_000, posicion_actual={"cantidad": 0, "valorizado": 0})

    assert client.buy_calls == []
    assert not isolated_trades_log.exists()


def test_sell_skipped_when_no_position(isolated_trades_log):
    client = FakeClient()
    executor = OrderExecutor(client, _risk_manager(), dry_run=False)
    signal = TradeSignal("GGAL", Signal.SELL, precio=100.0, motivo="test")

    executor.handle_signal(signal, portfolio_value=100_000, posicion_actual={"cantidad": 0, "valorizado": 0})

    assert client.sell_calls == []


def test_sell_sells_full_position_when_risk_allows(isolated_trades_log):
    client = FakeClient()
    executor = OrderExecutor(client, _risk_manager(), dry_run=False)
    signal = TradeSignal("GGAL", Signal.SELL, precio=100.0, motivo="test")

    executor.handle_signal(signal, portfolio_value=100_000, posicion_actual={"cantidad": 7, "valorizado": 700})

    assert client.sell_calls == [("GGAL", 7, 100.0)]


def test_hold_signal_does_nothing(isolated_trades_log):
    client = FakeClient()
    executor = OrderExecutor(client, _risk_manager(), dry_run=False)
    signal = TradeSignal("GGAL", Signal.HOLD, precio=100.0, motivo="sin señal")

    result = executor.handle_signal(signal, portfolio_value=100_000, posicion_actual={"cantidad": 0, "valorizado": 0})

    assert result is None
    assert client.buy_calls == [] and client.sell_calls == []
    assert not isolated_trades_log.exists()
