import csv
from datetime import datetime

import pandas as pd
import pytest

import iol_bot.main as main_module
import iol_bot.signals_log as signals_log_module
from iol_bot.main import BYMA_TZ, _intentar_rotacion, build_posiciones_por_simbolo, is_market_open, run_cycle
from iol_bot.strategy import Signal, TradeSignal


def _byma_datetime(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=BYMA_TZ)


def test_is_market_open_before_1030_is_closed():
    # BYMA adelantó la apertura a las 10:30 (antes 11:00) -- 10:29 todavía no abrió.
    assert not is_market_open(_byma_datetime(2026, 8, 14, 10, 29))


def test_is_market_open_at_1030_is_open():
    assert is_market_open(_byma_datetime(2026, 8, 14, 10, 30))


def test_is_market_open_mid_session_is_open():
    assert is_market_open(_byma_datetime(2026, 8, 14, 13, 0))


def test_is_market_open_at_1700_is_closed():
    assert not is_market_open(_byma_datetime(2026, 8, 14, 17, 0))


def test_is_market_open_weekend_is_closed_regardless_of_hour():
    sabado = _byma_datetime(2026, 8, 15, 12, 0)  # 2026-08-15 es sábado
    assert sabado.weekday() == 5
    assert not is_market_open(sabado)


@pytest.fixture(autouse=True)
def isolated_signals_log(tmp_path, monkeypatch):
    log_path = tmp_path / "signals.csv"
    monkeypatch.setattr(signals_log_module, "SIGNALS_LOG", log_path)
    monkeypatch.setattr(signals_log_module, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(main_module, "EQUITY_LOG", tmp_path / "equity.csv")
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


def test_scores_del_ranking_actual_excludes_nan_scores(monkeypatch):
    # Un score_total NaN (símbolo en cartera con historia de precios insuficiente) debe tratarse
    # como ausente, no como un valor comparable -- ver el comentario de la función: cualquier
    # comparación con NaN da False en Python, lo que rompía find_rotation_candidate si un NaN
    # quedaba fijado como "peor score visto".
    ranking_hoy = pd.DataFrame({"simbolo": ["BUENO", "SIN_DATOS"], "score_total": [72.5, float("nan")]})
    monkeypatch.setattr(main_module, "get_current_ranking", lambda: ranking_hoy)

    scores = main_module._scores_del_ranking_actual()

    assert scores == {"BUENO": 72.5}


class FakeStrategy:
    def __init__(self, signal=Signal.HOLD):
        self.signal = signal

    def evaluate(self, simbolo, price_df):
        return TradeSignal(simbolo, self.signal, precio=100.0, motivo="test")


class FakeLimits:
    take_profit_pct = 8
    stop_loss_pct = 5
    rotacion_habilitada = False
    min_mejora_score_rotacion = 15


class FakeRiskManager:
    def __init__(self):
        self.updated_with = None
        self.limits = FakeLimits()

    def update_portfolio_value(self, value):
        self.updated_with = value


class FakeExecutor:
    def __init__(self, resultado_venta=None):
        self.handled = []
        self._resultado_venta = resultado_venta

    def handle_signal(self, trade_signal, portfolio_value, posicion_actual, exposicion_total_actual=0.0):
        self.handled.append((trade_signal, portfolio_value, posicion_actual, exposicion_total_actual))
        return self._resultado_venta


def test_build_posiciones_por_simbolo():
    portafolio = {"activos": [{"titulo": {"simbolo": "GGAL"}, "cantidad": 5, "valorizado": 500, "ppc": 90.0}]}
    posiciones = build_posiciones_por_simbolo(portafolio)
    assert posiciones == {"GGAL": {"cantidad": 5, "valorizado": 500, "ppc": 90.0}}


def test_intentar_rotacion_sells_weakest_qualifying_position_and_updates_state():
    class RiskManagerStub:
        limits = FakeLimits()

    posiciones = {
        "DEBIL": {"cantidad": 10, "valorizado": 1000.0, "ppc": 100.0},
        "OTRA": {"cantidad": 5, "valorizado": 5000.0, "ppc": 900.0},
    }
    scores = {"CANDIDATO": 90.0, "DEBIL": 20.0, "OTRA": 85.0}  # OTRA no califica (diferencia de score < 15)
    executor = FakeExecutor(resultado_venta={"ok": True})

    nueva_exposicion = _intentar_rotacion(
        executor, RiskManagerStub(), "CANDIDATO", posiciones, portfolio_value=100_000,
        exposicion_total_actual=6000.0, scores_por_simbolo=scores,
    )

    assert "DEBIL" not in posiciones  # se removió de la cartera local (se vendió)
    assert "OTRA" in posiciones  # no calificaba, no se toca
    assert nueva_exposicion == 6000.0 - 1000.0  # se descontó el valorizado de DEBIL

    venta_signal = executor.handled[0][0]
    assert venta_signal.simbolo == "DEBIL"
    assert venta_signal.signal == Signal.SELL


def test_intentar_rotacion_does_nothing_when_no_candidate_qualifies():
    class RiskManagerStub:
        limits = FakeLimits()

    posiciones = {"A": {"cantidad": 10, "valorizado": 1000.0, "ppc": 100.0}}
    scores = {"CANDIDATO": 40.0, "A": 35.0}  # diferencia de score insuficiente (< 15)
    executor = FakeExecutor()

    nueva_exposicion = _intentar_rotacion(
        executor, RiskManagerStub(), "CANDIDATO", posiciones, portfolio_value=100_000,
        exposicion_total_actual=1000.0, scores_por_simbolo=scores,
    )

    assert nueva_exposicion == 1000.0  # sin cambios
    assert executor.handled == []
    assert "A" in posiciones


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
    trade_signal, portfolio_value, posicion_actual, exposicion_total_actual = executor.handled[0]
    assert trade_signal.simbolo == "GGAL"
    assert portfolio_value == 100_000
    assert posicion_actual == {"cantidad": 10, "valorizado": 1000, "ppc": 99.0}
    assert exposicion_total_actual == 1000

    with open(isolated_signals_log, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "GGAL"
    assert rows[1][5] == "hold"

    with open(main_module.EQUITY_LOG, newline="", encoding="utf-8") as f:
        equity_rows = list(csv.reader(f))
    assert equity_rows[0] == ["timestamp", "valorizado_total"]
    assert equity_rows[1][1] == "100000.0"


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

    trade_signal, _, _, _ = executor.handled[0]
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

    trade_signal, _, _, _ = executor.handled[0]
    assert trade_signal.signal == Signal.HOLD
    assert "ya en posición" in trade_signal.motivo
