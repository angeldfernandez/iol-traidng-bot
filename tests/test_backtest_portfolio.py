from iol_bot.backtest_portfolio import BacktestPortfolio, CostModel


def _cost_model(comision_pct=1.0, derechos_mercado_pct=0.5, slippage_pct=0.0):
    return CostModel(comision_pct=comision_pct, derechos_mercado_pct=derechos_mercado_pct, slippage_pct=slippage_pct)


def test_buy_applies_commission_and_derechos_exactly():
    pp = BacktestPortfolio(10_000, cost_model=_cost_model())
    ok = pp.buy("2024-01-01", "GGAL", 10, 100.0)

    assert ok is True
    # monto_bruto=1000, comision=10 (1%), derechos=5 (0.5%) -> costo_total=1015
    assert pp.cash == 10_000 - 1015.0
    assert pp.positions["GGAL"]["cantidad"] == 10
    assert pp.positions["GGAL"]["costo_promedio"] == 101.5  # 1015/10


def test_sell_applies_commission_and_derechos_exactly():
    pp = BacktestPortfolio(10_000, cost_model=_cost_model())
    pp.buy("2024-01-01", "GGAL", 10, 100.0)  # costo_promedio=101.5, cash=8985.0

    ok, pnl = pp.sell("2024-01-02", "GGAL", 10, 110.0)

    assert ok is True
    # monto_bruto=1100, comision=11, derechos=5.5 -> ingreso_neto=1083.5
    # pnl = 1083.5 - 10*101.5 = 68.5
    assert pnl == 68.5
    assert pp.cash == (10_000 - 1015.0) + 1083.5
    assert "GGAL" not in pp.positions


def test_slippage_makes_buys_more_expensive_and_sells_cheaper():
    pp_sin_slippage = BacktestPortfolio(10_000, cost_model=_cost_model(comision_pct=0, derechos_mercado_pct=0, slippage_pct=0))
    pp_con_slippage = BacktestPortfolio(10_000, cost_model=_cost_model(comision_pct=0, derechos_mercado_pct=0, slippage_pct=1.0))

    pp_sin_slippage.buy("2024-01-01", "GGAL", 10, 100.0)
    pp_con_slippage.buy("2024-01-01", "GGAL", 10, 100.0)

    # con 1% de slippage, se paga 101 por unidad en vez de 100 -> cuesta más
    assert pp_con_slippage.cash < pp_sin_slippage.cash

    pp_sin_slippage.sell("2024-01-02", "GGAL", 10, 100.0)
    pp_con_slippage.sell("2024-01-02", "GGAL", 10, 100.0)

    # simétricamente, al vender con slippage se cobra menos (99 en vez de 100)
    assert pp_con_slippage.cash < pp_sin_slippage.cash


def test_buy_rejected_when_costo_total_exceeds_cash():
    pp = BacktestPortfolio(1_000, cost_model=_cost_model())  # costo_total sería 1015 > 1000
    ok = pp.buy("2024-01-01", "GGAL", 10, 100.0)

    assert ok is False
    assert pp.cash == 1_000
    assert pp.positions == {}


def test_partial_sell_keeps_costo_promedio_unchanged():
    pp = BacktestPortfolio(100_000, cost_model=_cost_model(comision_pct=0, derechos_mercado_pct=0, slippage_pct=0))
    pp.buy("2024-01-01", "GGAL", 10, 100.0)

    ok, pnl = pp.sell("2024-01-02", "GGAL", 4, 110.0)

    assert ok is True
    assert pnl == 40.0  # (110-100)*4, sin costos en este caso
    assert pp.positions["GGAL"] == {"cantidad": 6, "costo_promedio": 100.0}


def test_sell_fails_when_no_position_or_insufficient_quantity():
    pp = BacktestPortfolio(10_000, cost_model=_cost_model())
    ok, pnl = pp.sell("2024-01-01", "GGAL", 5, 100.0)
    assert ok is False
    assert pnl == 0.0

    pp.buy("2024-01-01", "GGAL", 3, 100.0)
    ok, pnl = pp.sell("2024-01-02", "GGAL", 5, 100.0)
    assert ok is False


def test_trade_log_records_every_execution():
    pp = BacktestPortfolio(10_000, cost_model=_cost_model())
    pp.buy("2024-01-01", "GGAL", 10, 100.0)
    pp.sell("2024-01-02", "GGAL", 10, 110.0)

    assert len(pp.trade_log) == 2
    assert pp.trade_log[0]["lado"] == "COMPRA"
    assert pp.trade_log[0]["pnl_realizado"] is None
    assert pp.trade_log[1]["lado"] == "VENTA"
    assert pp.trade_log[1]["pnl_realizado"] == 68.5


def test_valorizado_total_uses_current_prices_and_falls_back_to_cost():
    pp = BacktestPortfolio(100_000, cost_model=_cost_model(comision_pct=0, derechos_mercado_pct=0, slippage_pct=0))
    pp.buy("2024-01-01", "GGAL", 10, 100.0)  # cash: 99_000

    valorizado = pp.valorizado_total({"GGAL": 150.0})
    assert valorizado == 99_000 + 10 * 150.0

    # sin precio para GGAL -> usa costo_promedio
    assert pp.valorizado_total({}) == 99_000 + 10 * 100.0


def test_exposicion_reflects_marked_to_market_value():
    pp = BacktestPortfolio(100_000, cost_model=_cost_model())
    pp.buy("2024-01-01", "GGAL", 10, 100.0)

    assert pp.exposicion("GGAL", precio_actual=150.0) == 1500.0
    assert pp.exposicion("YPFD", precio_actual=100.0) == 0.0
