from iol_bot.paper_portfolio import PaperPortfolio, load_status


def test_starts_with_initial_cash_and_no_positions(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    assert pp.cash == 100_000
    assert pp.positions == {}
    assert pp.valorizado_total({}) == 100_000


def test_buy_reduces_cash_and_opens_position(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    ok = pp.buy("GGAL", 10, 100.0)
    assert ok is True
    assert pp.cash == 99_000
    assert pp.positions["GGAL"] == {"cantidad": 10, "costo_promedio": 100.0}


def test_buy_fails_when_not_enough_cash():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        pp = PaperPortfolio(Path(d) / "paper.json", initial_cash=100)
        ok = pp.buy("GGAL", 10, 100.0)  # costaría 1000, solo hay 100
        assert ok is False
        assert pp.cash == 100
        assert pp.positions == {}


def test_buy_averages_cost_on_second_purchase(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    pp.buy("GGAL", 10, 100.0)  # costo promedio 100
    pp.buy("GGAL", 10, 200.0)  # costo promedio (1000+2000)/20 = 150
    assert pp.positions["GGAL"]["cantidad"] == 20
    assert pp.positions["GGAL"]["costo_promedio"] == 150.0


def test_sell_full_position_realizes_pnl_and_clears_position(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    pp.buy("GGAL", 10, 100.0)
    ok, pnl = pp.sell("GGAL", 10, 120.0)
    assert ok is True
    assert pnl == 200.0  # (120-100)*10
    assert "GGAL" not in pp.positions
    assert pp.cash == 100_000 - 1000 + 1200


def test_sell_partial_position_keeps_remainder():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        pp = PaperPortfolio(Path(d) / "paper.json", initial_cash=100_000)
        pp.buy("GGAL", 10, 100.0)
        ok, pnl = pp.sell("GGAL", 4, 110.0)
        assert ok is True
        assert pnl == 40.0  # (110-100)*4
        assert pp.positions["GGAL"] == {"cantidad": 6, "costo_promedio": 100.0}


def test_sell_fails_when_no_position_or_insufficient_quantity(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    ok, pnl = pp.sell("GGAL", 5, 100.0)
    assert ok is False
    assert pnl == 0.0

    pp.buy("GGAL", 3, 100.0)
    ok, pnl = pp.sell("GGAL", 5, 100.0)  # tiene 3, pide vender 5
    assert ok is False


def test_exposicion_reflects_marked_to_market_value(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    pp.buy("GGAL", 10, 100.0)
    assert pp.exposicion("GGAL", precio_actual=150.0) == 1500.0
    assert pp.exposicion("YPFD", precio_actual=100.0) == 0.0


def test_valorizado_total_uses_current_prices_and_falls_back_to_cost(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    pp.buy("GGAL", 10, 100.0)  # cash: 99_000
    pp.buy("YPFD", 5, 200.0)  # cash: 98_000

    valorizado = pp.valorizado_total({"GGAL": 120.0})  # YPFD sin precio -> usa costo promedio
    assert valorizado == 98_000 + 10 * 120.0 + 5 * 200.0


def test_status_reports_pnl_vs_initial_cash(tmp_path):
    pp = PaperPortfolio(tmp_path / "paper.json", initial_cash=100_000)
    pp.buy("GGAL", 10, 100.0)
    status = pp.status({"GGAL": 150.0})
    assert status["valorizado_total"] == 99_000 + 1500
    assert status["pnl_total"] == status["valorizado_total"] - 100_000
    assert round(status["pnl_total_pct"], 4) == round(status["pnl_total"] / 100_000 * 100, 4)


def test_init_persists_initial_state_immediately_when_no_file_exists(tmp_path):
    state_path = tmp_path / "paper.json"
    assert not state_path.exists()

    PaperPortfolio(state_path, initial_cash=500_000)

    assert state_path.exists()  # no hace falta esperar a la primera compra/venta
    status = load_status(state_path)
    assert status["cash"] == 500_000
    assert status["positions"] == {}


def test_init_does_not_overwrite_existing_state(tmp_path):
    state_path = tmp_path / "paper.json"
    pp1 = PaperPortfolio(state_path, initial_cash=100_000)
    pp1.buy("GGAL", 10, 100.0)

    # Reconstruir con un initial_cash distinto no debe pisar lo ya persistido.
    PaperPortfolio(state_path, initial_cash=999_999)

    status = load_status(state_path)
    assert status["cash"] == 99_000
    assert status["positions"]["GGAL"]["cantidad"] == 10


def test_state_persists_across_instances(tmp_path):
    state_path = tmp_path / "paper.json"
    pp1 = PaperPortfolio(state_path, initial_cash=100_000)
    pp1.buy("GGAL", 10, 100.0)

    pp2 = PaperPortfolio(state_path, initial_cash=100_000)
    assert pp2.cash == 99_000
    assert pp2.positions["GGAL"] == {"cantidad": 10, "costo_promedio": 100.0}


def test_load_status_standalone(tmp_path):
    state_path = tmp_path / "paper.json"
    assert load_status(state_path) is None

    pp = PaperPortfolio(state_path, initial_cash=100_000)
    pp.buy("GGAL", 10, 100.0)

    status = load_status(state_path)
    assert status["cash"] == 99_000
    assert status["positions"]["GGAL"]["cantidad"] == 10
