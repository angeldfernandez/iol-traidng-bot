from iol_bot.pnl import daily_pnl_pesos


def test_daily_pnl_pesos_positive_variation():
    # Precio subió 10% hoy, valorizado actual 1100 -> ayer valía 1000 -> ganó 100
    assert round(daily_pnl_pesos(1100, 10), 2) == 100.0


def test_daily_pnl_pesos_negative_variation():
    # Precio bajó 10% hoy, valorizado actual 900 -> ayer valía 1000 -> perdió 100
    assert round(daily_pnl_pesos(900, -10), 2) == -100.0


def test_daily_pnl_pesos_zero_variation():
    assert daily_pnl_pesos(1000, 0) == 0.0


def test_daily_pnl_pesos_handles_none():
    assert daily_pnl_pesos(None, 5) == 0.0
    assert daily_pnl_pesos(1000, None) == 0.0


def test_daily_pnl_pesos_handles_total_wipeout_denominator():
    assert daily_pnl_pesos(0, -100) == 0.0
