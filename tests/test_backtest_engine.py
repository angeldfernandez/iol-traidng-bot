import numpy as np
import pandas as pd

from iol_bot.backtest_engine import run_backtest
from iol_bot.backtest_portfolio import CostModel
from iol_bot.config import RiskLimits
from iol_bot.strategy import Signal, SmaCrossoverRsiStrategy, TradeSignal

ZERO_COSTS = CostModel(comision_pct=0.0, derechos_mercado_pct=0.0, slippage_pct=0.0)


def _limits(**overrides):
    defaults = dict(
        max_monto_por_orden_pct=100,
        max_exposicion_por_simbolo_pct=100,
        max_perdida_diaria_pct=5,
        take_profit_pct=100,
        stop_loss_pct=100,
    )
    defaults.update(overrides)
    return RiskLimits(**defaults)


def _price_df(fechas, cierres):
    cierres = np.asarray(cierres, dtype=float)
    return pd.DataFrame(
        {
            "fecha": pd.to_datetime(fechas),
            "apertura": cierres,
            "maximo": cierres * 1.01,
            "minimo": cierres * 0.99,
            "cierre": cierres,
            "variacion": np.zeros(len(cierres)),
        }
    )


class FakeStrategyBuy:
    """Siempre señaliza BUY — para testear el motor (sizing/orden/circuit breaker) sin depender
    de si SmaCrossoverRsiStrategy dispararía una señal ese día puntual."""

    def evaluate(self, simbolo, price_df):
        return TradeSignal(simbolo, Signal.BUY, precio=float(price_df["cierre"].iloc[-1]), motivo="test")


def test_look_ahead_bias_mutating_future_row_does_not_change_earlier_results():
    n = 90
    fechas = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(42)
    retornos = rng.normal(0.002, 0.015, size=n)  # tendencia levemente alcista con ruido, determinístico (seed fija)
    cierres = 100.0 * np.cumprod(1 + retornos)

    baseline_df = _price_df(fechas, cierres)

    cierres_mutados = cierres.copy()
    cierres_mutados[75] *= 10  # salto enorme, muy después del punto de corte que se va a comparar
    variant_df = _price_df(fechas, cierres_mutados)

    strategy = SmaCrossoverRsiStrategy()
    limits = _limits()
    corte = fechas[60]

    resultado_base = run_backtest(strategy, {"GGAL": baseline_df}, fechas[0], fechas[-1], 1_000_000, ZERO_COSTS, limits)
    resultado_variante = run_backtest(strategy, {"GGAL": variant_df}, fechas[0], fechas[-1], 1_000_000, ZERO_COSTS, limits)

    equity_base_hasta_corte = resultado_base.equity_curve[resultado_base.equity_curve.index <= corte]
    equity_variante_hasta_corte = resultado_variante.equity_curve[resultado_variante.equity_curve.index <= corte]
    pd.testing.assert_series_equal(equity_base_hasta_corte, equity_variante_hasta_corte)

    trades_base_hasta_corte = resultado_base.trades[resultado_base.trades["fecha"] <= corte] if not resultado_base.trades.empty else resultado_base.trades
    trades_variante_hasta_corte = (
        resultado_variante.trades[resultado_variante.trades["fecha"] <= corte] if not resultado_variante.trades.empty else resultado_variante.trades
    )
    # Normalizar pnl_realizado a numérico: si en un run no hubo ninguna venta antes del corte,
    # pandas infiere la columna completa como dtype "object" con None; si en el otro sí hubo,
    # queda float64 con NaN. Mismo significado ("sin venta todavía"), representación distinta —
    # no es la propiedad que este test verifica (esa es que los VALORES hasta el corte coincidan).
    trades_base_hasta_corte = trades_base_hasta_corte.assign(
        pnl_realizado=pd.to_numeric(trades_base_hasta_corte["pnl_realizado"], errors="coerce")
    )
    trades_variante_hasta_corte = trades_variante_hasta_corte.assign(
        pnl_realizado=pd.to_numeric(trades_variante_hasta_corte["pnl_realizado"], errors="coerce")
    )
    pd.testing.assert_frame_equal(trades_base_hasta_corte.reset_index(drop=True), trades_variante_hasta_corte.reset_index(drop=True))


def test_deterministic_symbol_order_determines_who_gets_filled_with_limited_cash():
    fecha = pd.Timestamp("2024-01-02")
    price_data = {
        "B": _price_df([fecha], [100_000.0]),
        "A": _price_df([fecha], [100_000.0]),
    }
    limits = _limits()

    # orden alfabético por defecto: A se procesa primero y se queda con el cash disponible.
    resultado = run_backtest(FakeStrategyBuy(), price_data, fecha, fecha, 150_000, ZERO_COSTS, limits)
    trades = resultado.trades
    assert set(trades[trades["lado"] == "COMPRA"]["simbolo"]) == {"A"}

    # invirtiendo el orden explícito, ahora es B el que se queda con el cash.
    resultado_b_primero = run_backtest(
        FakeStrategyBuy(), price_data, fecha, fecha, 150_000, ZERO_COSTS, limits, orden_simbolos=["B", "A"]
    )
    trades_b = resultado_b_primero.trades
    assert set(trades_b[trades_b["lado"] == "COMPRA"]["simbolo"]) == {"B"}


def test_circuit_breaker_blocks_new_buys_after_daily_loss_breach():
    dia1 = pd.Timestamp("2024-01-02")
    dia2 = pd.Timestamp("2024-01-03")

    price_data = {
        "A": _price_df([dia1, dia2], [100.0, 1.0]),  # se derrumba -99% de un día para el otro
        "B": _price_df([dia2], [100.0]),  # recién aparece el día 2 (sin posición previa)
    }
    limits = _limits(max_perdida_diaria_pct=5)  # 5% de tolerancia, la caída de A es mucho mayor

    resultado = run_backtest(FakeStrategyBuy(), price_data, dia1, dia2, 100_000, ZERO_COSTS, limits)

    assert dia2 in resultado.halted_days
    # A ya estaba comprado desde el día 1 y no se vende (stop_loss_pct=100 no se dispara);
    # B nunca llega a comprarse porque el circuit breaker bloqueó las compras nuevas del día 2.
    compras = resultado.trades[resultado.trades["lado"] == "COMPRA"]
    assert set(compras["simbolo"]) == {"A"}
    assert (compras["fecha"] == dia1).all()


def test_symbol_with_mid_window_gap_is_skipped_not_crashed():
    dia1 = pd.Timestamp("2024-01-02")
    dia2 = pd.Timestamp("2024-01-03")
    dia3 = pd.Timestamp("2024-01-04")

    price_data = {
        # a "GAP" le falta el día 2 (feriado puntual / dato faltante) — no debe romper el loop
        "GAP": pd.concat([_price_df([dia1], [100.0]), _price_df([dia3], [105.0])], ignore_index=True),
    }
    limits = _limits()

    resultado = run_backtest(FakeStrategyBuy(), price_data, dia1, dia3, 100_000, ZERO_COSTS, limits)

    assert list(resultado.equity_curve.index) == [dia1, dia3]  # dia2 ni aparece, no hay dato


def test_empty_price_data_returns_empty_result_without_crashing():
    limits = _limits()
    resultado = run_backtest(FakeStrategyBuy(), {}, pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-02"), 100_000, ZERO_COSTS, limits)

    assert resultado.equity_curve.empty
    assert resultado.trades.empty
    assert resultado.halted_days == []
