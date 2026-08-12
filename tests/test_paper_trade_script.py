from iol_bot.client import IOLApiError
from iol_bot.paper_portfolio import PaperPortfolio
from scripts.paper_trade import _mark_to_market_final


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
