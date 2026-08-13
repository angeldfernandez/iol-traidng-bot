import pandas as pd
import pytest

import iol_bot.main as main_module
import scripts.paper_trade as paper_trade_module
from iol_bot.client import IOLApiError
from iol_bot.config import RiskLimits
from iol_bot.paper_portfolio import PaperPortfolio
from iol_bot.risk import RiskManager
from iol_bot.strategy import Signal, TradeSignal
from scripts.paper_trade import _mark_to_market_final, run_cycle


@pytest.fixture(autouse=True)
def isolated_paper_logs(tmp_path, monkeypatch):
    # run_cycle loggea a PAPER_TRADES_LOG/PAPER_SIGNALS_LOG (constantes de módulo, no parametrizadas
    # por la cartera) — sin esto, correr estos tests escribe basura en los logs REALES del usuario.
    monkeypatch.setattr(paper_trade_module, "PAPER_TRADES_LOG", tmp_path / "paper_trades.csv")
    monkeypatch.setattr(paper_trade_module, "PAPER_SIGNALS_LOG", tmp_path / "paper_signals.csv")


class FakeClient:
    def __init__(self, precios=None, error_para=None):
        self.precios = precios or {}
        self.error_para = error_para or set()
        self.calls = []

    def cotizacion(self, simbolo, **kwargs):
        self.calls.append(simbolo)
        if simbolo in self.error_para:
            raise IOLApiError("boom")
        return {"ultimoPrecio": self.precios[simbolo]}


def test_mark_to_market_final_fetches_price_per_held_symbol(tmp_path):
    portfolio = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    portfolio.buy("GGAL", 10, 100.0)
    portfolio.buy("YPFD", 5, 200.0)

    client = FakeClient(precios={"GGAL": 120.0, "YPFD": 190.0})
    precios = _mark_to_market_final(client, portfolio)

    assert precios == {"GGAL": 120.0, "YPFD": 190.0}
    assert set(client.calls) == {"GGAL", "YPFD"}


def test_mark_to_market_final_falls_back_gracefully_on_api_error(tmp_path):
    portfolio = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    portfolio.buy("GGAL", 10, 100.0)
    portfolio.buy("YPFD", 5, 200.0)

    client = FakeClient(precios={"GGAL": 120.0}, error_para={"YPFD"})
    precios = _mark_to_market_final(client, portfolio)

    assert precios == {"GGAL": 120.0}  # YPFD queda afuera; valorizado_total cae a costo_promedio

    status = portfolio.status(precios)
    valorizado_esperado = portfolio.cash + 10 * 120.0 + 5 * 200.0  # YPFD a costo, no a precio real
    assert status["valorizado_total"] == valorizado_esperado


def test_mark_to_market_final_no_positions_returns_empty_dict(tmp_path):
    portfolio = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    client = FakeClient()
    assert _mark_to_market_final(client, portfolio) == {}
    assert client.calls == []


class FakeStrategyBuy:
    def evaluate(self, simbolo, price_df):
        return TradeSignal(simbolo, Signal.BUY, precio=float(price_df["cierre"].iloc[-1]), motivo="test")


class FakeStrategyHold:
    def evaluate(self, simbolo, price_df):
        return TradeSignal(simbolo, Signal.HOLD, precio=float(price_df["cierre"].iloc[-1]), motivo="test")


class FakeClientCotizacion:
    def cotizacion(self, simbolo, **kwargs):
        return {"ultimoPrecio": 100.0}


def test_run_cycle_marks_all_positions_to_market_even_if_in_watchlist(tmp_path, monkeypatch):
    # Antes del fix del 2026-08-13, una posición que seguía en el watchlist no recibía un precio
    # fresco vía cotizacion() al PRINCIPIO del ciclo (recién más tarde, dentro del loop) — el
    # circuit breaker/daily P&L la medía todavía a costo de compra en ese primer momento, dando un
    # resultado inconsistente con el valorizado total real de la cartera.
    monkeypatch.setattr(
        paper_trade_module,
        "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: pd.DataFrame({"cierre": [100.0]}),
    )

    portfolio = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    portfolio.buy("GGAL", 10, 90.0)  # posición ya abierta, costo_promedio=90, cash=99_100

    limits = RiskLimits(
        max_monto_por_orden_pct=100, max_exposicion_por_simbolo_pct=100, max_perdida_diaria_pct=100,
        take_profit_pct=100, stop_loss_pct=100,
    )
    risk_manager = RiskManager(limits)  # sin state_path: no toca disco

    client = FakeClient(precios={"GGAL": 123.0})
    watchlist = [{"simbolo": "GGAL", "mercado": "bCBA", "ultimo_precio": 100.0}]  # GGAL SÍ está en el watchlist

    run_cycle(client, FakeStrategyHold(), risk_manager, portfolio, watchlist)

    assert "GGAL" in client.calls  # se le pidió cotización directa aunque estuviera en el watchlist
    # baseline del día = cash(99_100) + 10 x precio fresco(123), NO a costo de compra (90)
    assert risk_manager.status()["baseline_value"] == pytest.approx(99_100 + 10 * 123.0)


def test_run_cycle_rotates_weaker_position_to_fund_better_ranked_candidate(tmp_path, monkeypatch):
    # CANDIDATO (fuera de cartera) está mucho mejor rankeado que DEBIL (ya en cartera).
    ranking_hoy = pd.DataFrame({"simbolo": ["CANDIDATO", "DEBIL"], "score_total": [90.0, 20.0], "eligible": [True, True]})
    monkeypatch.setattr(main_module, "get_current_ranking", lambda: ranking_hoy)
    monkeypatch.setattr(
        paper_trade_module,
        "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: pd.DataFrame({"cierre": [100.0]}),
    )

    portfolio = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    portfolio.buy("DEBIL", 900, 100.0)  # 90_000 invertidos, cash=10_000

    limits = RiskLimits(
        max_monto_por_orden_pct=100,
        max_exposicion_por_simbolo_pct=100,
        max_perdida_diaria_pct=100,
        take_profit_pct=100,
        stop_loss_pct=100,
        max_exposicion_total_pct=90,  # exactamente lo ya invertido -> CANDIDATO no entra sin rotar
        rotacion_habilitada=True,
        min_mejora_score_rotacion=15,
    )
    risk_manager = RiskManager(limits)
    watchlist = [{"simbolo": "CANDIDATO", "mercado": "bCBA", "ultimo_precio": 100.0}]

    run_cycle(FakeClientCotizacion(), FakeStrategyBuy(), risk_manager, portfolio, watchlist)

    assert "DEBIL" not in portfolio.positions  # se vendió (sin pérdida) para liberar lugar
    assert "CANDIDATO" in portfolio.positions  # y el candidato mejor rankeado se pudo comprar
    assert portfolio.positions["CANDIDATO"]["cantidad"] == 900


def test_run_cycle_does_not_rotate_when_disabled(tmp_path, monkeypatch):
    # Mismo escenario que el test anterior, pero con rotacion_habilitada=False (default): CANDIDATO
    # se queda sin comprar, DEBIL no se toca.
    ranking_hoy = pd.DataFrame({"simbolo": ["CANDIDATO", "DEBIL"], "score_total": [90.0, 20.0], "eligible": [True, True]})
    monkeypatch.setattr(main_module, "get_current_ranking", lambda: ranking_hoy)
    monkeypatch.setattr(
        paper_trade_module,
        "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: pd.DataFrame({"cierre": [100.0]}),
    )

    portfolio = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    portfolio.buy("DEBIL", 900, 100.0)

    limits = RiskLimits(
        max_monto_por_orden_pct=100,
        max_exposicion_por_simbolo_pct=100,
        max_perdida_diaria_pct=100,
        take_profit_pct=100,
        stop_loss_pct=100,
        max_exposicion_total_pct=90,
        rotacion_habilitada=False,
        min_mejora_score_rotacion=15,
    )
    risk_manager = RiskManager(limits)
    watchlist = [{"simbolo": "CANDIDATO", "mercado": "bCBA", "ultimo_precio": 100.0}]

    run_cycle(FakeClientCotizacion(), FakeStrategyBuy(), risk_manager, portfolio, watchlist)

    assert "DEBIL" in portfolio.positions
    assert "CANDIDATO" not in portfolio.positions
