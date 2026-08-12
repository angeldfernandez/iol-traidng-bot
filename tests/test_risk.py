from iol_bot.config import RiskLimits
from iol_bot.risk import (
    RiskManager,
    apply_position_override,
    calc_buy_quantity,
    check_daily_circuit_breaker,
    check_position_exit,
)
from iol_bot.strategy import Signal, TradeSignal


def test_calc_buy_quantity_respects_monto_max():
    # 1% de 100_000 = 1000 ARS de tope por orden
    cantidad = calc_buy_quantity(
        precio=100, monto_max_orden_pct=1, portfolio_value=100_000, exposicion_actual_simbolo=0, max_exposicion_pct=50
    )
    assert cantidad == 10  # 1000 / 100


def test_calc_buy_quantity_respects_exposicion_max():
    # tope por orden: 100% de la cartera (no ata). tope de exposición: 10% de 100_000 = 10_000,
    # ya hay 9_500 expuestos -> solo quedan 500 disponibles
    cantidad = calc_buy_quantity(
        precio=100,
        monto_max_orden_pct=100,
        portfolio_value=100_000,
        exposicion_actual_simbolo=9_500,
        max_exposicion_pct=10,
    )
    assert cantidad == 5  # 500 / 100


def test_calc_buy_quantity_zero_when_no_margin_left():
    cantidad = calc_buy_quantity(
        precio=100,
        monto_max_orden_pct=100,
        portfolio_value=100_000,
        exposicion_actual_simbolo=10_000,
        max_exposicion_pct=10,
    )
    assert cantidad == 0


def test_check_daily_circuit_breaker_triggers_above_threshold():
    assert check_daily_circuit_breaker(daily_pnl=-6_000, portfolio_value=100_000, max_perdida_diaria_pct=5) is True
    assert check_daily_circuit_breaker(daily_pnl=-4_000, portfolio_value=100_000, max_perdida_diaria_pct=5) is False


def _limits(**overrides):
    defaults = dict(
        max_monto_por_orden_pct=20,
        max_exposicion_por_simbolo_pct=20,
        max_perdida_diaria_pct=5,
        take_profit_pct=8,
        stop_loss_pct=5,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def test_check_position_exit_triggers_take_profit():
    debe_vender, motivo = check_position_exit(precio_actual=110, costo_promedio=100, take_profit_pct=8, stop_loss_pct=5)
    assert debe_vender is True
    assert "take-profit" in motivo


def test_check_position_exit_triggers_stop_loss():
    debe_vender, motivo = check_position_exit(precio_actual=94, costo_promedio=100, take_profit_pct=8, stop_loss_pct=5)
    assert debe_vender is True
    assert "stop-loss" in motivo


def test_check_position_exit_holds_within_band():
    debe_vender, motivo = check_position_exit(precio_actual=103, costo_promedio=100, take_profit_pct=8, stop_loss_pct=5)
    assert debe_vender is False
    assert motivo is None


def test_check_position_exit_handles_missing_costo_promedio():
    assert check_position_exit(precio_actual=100, costo_promedio=None, take_profit_pct=8, stop_loss_pct=5) == (False, None)
    assert check_position_exit(precio_actual=100, costo_promedio=0, take_profit_pct=8, stop_loss_pct=5) == (False, None)


def test_apply_position_override_passes_through_when_no_position():
    signal = TradeSignal("GGAL", Signal.BUY, precio=100.0, motivo="cruce alcista")
    resultado = apply_position_override(signal, cantidad_en_cartera=0, costo_promedio=None, limits=_limits())
    assert resultado is signal


def test_apply_position_override_forces_sell_on_take_profit():
    signal = TradeSignal("GGAL", Signal.BUY, precio=110.0, motivo="tendencia vigente")
    resultado = apply_position_override(signal, cantidad_en_cartera=10, costo_promedio=100.0, limits=_limits())
    assert resultado.signal == Signal.SELL
    assert "take-profit" in resultado.motivo


def test_apply_position_override_forces_sell_on_stop_loss():
    signal = TradeSignal("GGAL", Signal.HOLD, precio=94.0, motivo="sin señal")
    resultado = apply_position_override(signal, cantidad_en_cartera=10, costo_promedio=100.0, limits=_limits())
    assert resultado.signal == Signal.SELL
    assert "stop-loss" in resultado.motivo


def test_apply_position_override_downgrades_buy_to_hold_when_already_holding():
    signal = TradeSignal("GGAL", Signal.BUY, precio=102.0, motivo="tendencia vigente")
    resultado = apply_position_override(signal, cantidad_en_cartera=10, costo_promedio=100.0, limits=_limits())
    assert resultado.signal == Signal.HOLD
    assert "ya en posición" in resultado.motivo


def test_apply_position_override_lets_strategy_sell_through_when_holding():
    signal = TradeSignal("GGAL", Signal.SELL, precio=102.0, motivo="RSI sobrecomprado (75.0)")
    resultado = apply_position_override(signal, cantidad_en_cartera=10, costo_promedio=100.0, limits=_limits())
    assert resultado is signal  # ni TP ni SL disparan, se deja pasar la señal original de venta


def test_apply_position_override_lets_hold_through_when_no_trigger():
    signal = TradeSignal("GGAL", Signal.HOLD, precio=101.0, motivo="sin señal")
    resultado = apply_position_override(signal, cantidad_en_cartera=10, costo_promedio=100.0, limits=_limits())
    assert resultado is signal


def test_risk_manager_halts_trading_after_daily_loss_breach():
    rm = RiskManager(_limits())
    rm.update_portfolio_value(100_000)  # fija la línea base del día
    cantidad, motivo = rm.size_buy_order(precio=100, portfolio_value=100_000, exposicion_actual_simbolo=0)
    assert cantidad > 0
    assert not rm.is_halted()

    rm.update_portfolio_value(94_000)  # cartera cayó 6% respecto a la línea base
    assert rm.is_halted()

    cantidad, motivo = rm.size_buy_order(precio=100, portfolio_value=94_000, exposicion_actual_simbolo=0)
    assert cantidad == 0
    assert "circuit breaker" in motivo

    allowed, _ = rm.approve_sell()
    assert allowed is False


def test_risk_manager_reset_daily_clears_halt():
    rm = RiskManager(_limits())
    rm.update_portfolio_value(100_000)
    rm.update_portfolio_value(94_000)
    assert rm.is_halted()

    rm.reset_daily()
    assert not rm.is_halted()
    allowed, _ = rm.approve_sell()
    assert allowed is True


def test_risk_manager_stays_halted_even_if_value_recovers_same_day():
    rm = RiskManager(_limits())
    rm.update_portfolio_value(100_000)
    rm.update_portfolio_value(94_000)
    assert rm.is_halted()

    # El circuit breaker es "sticky" dentro del día: una vez disparado, sigue detenido aunque
    # la cartera se recupere — solo reset_daily() (nuevo día) lo vuelve a habilitar.
    rm.update_portfolio_value(99_000)
    assert rm.is_halted()


def test_risk_manager_persists_state_to_disk(tmp_path):
    state_path = tmp_path / "risk_state.json"
    rm = RiskManager(_limits(), state_path=state_path)
    rm.update_portfolio_value(100_000)
    rm.update_portfolio_value(94_000)

    assert state_path.exists()

    rm2 = RiskManager(_limits(), state_path=state_path)
    assert rm2.is_halted()
    assert rm2.status()["baseline_value"] == 100_000


def test_load_status_reads_persisted_state(tmp_path):
    from iol_bot.risk import load_status

    state_path = tmp_path / "risk_state.json"
    assert load_status(state_path) is None

    rm = RiskManager(_limits(), state_path=state_path)
    rm.update_portfolio_value(100_000)

    status = load_status(state_path)
    assert status["baseline_value"] == 100_000
    assert status["halted"] is False
