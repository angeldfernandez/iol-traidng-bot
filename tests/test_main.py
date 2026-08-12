import csv

import pandas as pd
import pytest

import iol_bot.main as main_module
import iol_bot.signals_log as signals_log_module
from iol_bot.main import build_posiciones_por_simbolo, run_cycle
from iol_bot.strategy import Signal, TradeSignal


@pytest.fixture(autouse=True)
def isolated_signals_log(tmp_path, monkeypatch):
    log_path = tmp_path / "signals.csv"
    monkeypatch.setattr(signals_log_module, "SIGNALS_LOG", log_path)
    monkeypatch.setattr(signals_log_module, "LOGS_DIR", tmp_path)
    return log_path


class FakeClient:
    def __init__(self):
        self.calls = []

    def estado_cuenta(self):
        return {"totalEnPesos": 100_000}

    def portafolio(self):
        return {"activos": [{"titulo": {"simbolo": "GGAL"}, "cantidad": 10, "valorizado": 1000, "ppc": 90.0}]}

    def serie_historica(self, **kwargs):
        self.calls.append(kwargs)
        return [{"fechaHora": "2024-01-01T00:00:00", "ultimoPrecio": 100}]


class FakeStrategy:
    def __init__(self, signal=Signal.HOLD):
        self.signal = signal

    def evaluate(self, simbolo, price_df):
        return TradeSignal(simbolo, self.signal, precio=100.0, motivo="test")


class FakeLimits:
    take_profit_pct = 8
    stop_loss_pct = 5


class FakeRiskManager:
    def __init__(self):
        self.updated_with = None
        self.limits = FakeLimits()

    def update_portfolio_value(self, value):
        self.updated_with = value


class FakeExecutor:
    def __init__(self):
        self.handled = []

    def handle_signal(self, trade_signal, portfolio_value, posicion_actual):
        self.handled.append((trade_signal, portfolio_value, posicion_actual))
        return None


def test_build_posiciones_por_simbolo():
    portafolio = {"activos": [{"titulo": {"simbolo": "GGAL"}, "cantidad": 5, "valorizado": 500, "ppc": 90.0}]}
    posiciones = build_posiciones_por_simbolo(portafolio)
    assert posiciones == {"GGAL": {"cantidad": 5, "valorizado": 500, "ppc": 90.0}}


def test_run_cycle_updates_risk_manager_and_dispatches_signals(isolated_signals_log, monkeypatch):
    client = FakeClient()
    # ppc pegado al precio actual (100) para que este test no dispare el override de TP/SL —
    # eso ya lo cubren los tests dedicados más abajo. Acá solo interesa el wiring general.
    client.portafolio = lambda: {
        "activos": [{"titulo": {"simbolo": "GGAL"}, "cantidad": 10, "valorizado": 1000, "ppc": 99.0}]
    }
    strategy = FakeStrategy()
    risk_manager = FakeRiskManager()
    executor = FakeExecutor()
    watchlist = [{"simbolo": "GGAL", "mercado": "bCBA"}]

    monkeypatch.setattr(
        main_module,
        "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: pd.DataFrame({"cierre": [100]}),
    )

    run_cycle(client, strategy, executor, risk_manager, watchlist)

    assert risk_manager.updated_with == 100_000
    assert len(executor.handled) == 1
    trade_signal, portfolio_value, posicion_actual = executor.handled[0]
    assert trade_signal.simbolo == "GGAL"
    assert portfolio_value == 100_000
    assert posicion_actual == {"cantidad": 10, "valorizado": 1000, "ppc": 99.0}

    with open(isolated_signals_log, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "GGAL"
    assert rows[1][5] == "hold"


def test_run_cycle_does_not_rebuy_symbol_already_held(isolated_signals_log, monkeypatch):
    # GGAL: cantidad=10, ppc=90.0, precio actual del signal=100 (+11% respecto al ppc, dentro de
    # la banda de TP=8%/SL=5% definida en FakeLimits... en realidad supera el TP, así que el
    # override debería forzar SELL en vez de solo bajar a HOLD. Se verifica eso explícitamente.
    client = FakeClient()
    strategy = FakeStrategy(signal=Signal.BUY)
    risk_manager = FakeRiskManager()
    executor = FakeExecutor()
    watchlist = [{"simbolo": "GGAL", "mercado": "bCBA"}]

    monkeypatch.setattr(
        main_module,
        "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: pd.DataFrame({"cierre": [100]}),
    )

    run_cycle(client, strategy, executor, risk_manager, watchlist)

    trade_signal, _, _ = executor.handled[0]
    # ppc=90, precio=100 -> +11.1%, por encima del take_profit_pct=8 de FakeLimits: fuerza SELL.
    assert trade_signal.signal == Signal.SELL
    assert "take-profit" in trade_signal.motivo


def test_run_cycle_downgrades_buy_to_hold_when_holding_without_hitting_tp_sl(isolated_signals_log, monkeypatch):
    client = FakeClient()
    # ppc muy cercano al precio actual (100) para no disparar TP/SL, solo probar el freno de recompra.
    client.portafolio = lambda: {
        "activos": [{"titulo": {"simbolo": "GGAL"}, "cantidad": 10, "valorizado": 1000, "ppc": 99.0}]
    }
    strategy = FakeStrategy(signal=Signal.BUY)
    risk_manager = FakeRiskManager()
    executor = FakeExecutor()
    watchlist = [{"simbolo": "GGAL", "mercado": "bCBA"}]

    monkeypatch.setattr(
        main_module,
        "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: pd.DataFrame({"cierre": [100]}),
    )

    run_cycle(client, strategy, executor, risk_manager, watchlist)

    trade_signal, _, _ = executor.handled[0]
    assert trade_signal.signal == Signal.HOLD
    assert "ya en posición" in trade_signal.motivo
