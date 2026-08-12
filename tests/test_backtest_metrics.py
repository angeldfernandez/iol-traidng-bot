import math

import numpy as np
import pandas as pd
import pytest

from iol_bot.backtest_metrics import compute_buy_and_hold_equity, compute_metrics
from iol_bot.backtest_portfolio import CostModel


def test_compute_metrics_empty_equity_returns_empty_dict():
    assert compute_metrics(pd.Series(dtype=float), pd.DataFrame(), pd.Series(dtype=float)) == {}


def test_retorno_total_and_cagr_closed_form():
    equity = pd.Series({pd.Timestamp("2022-01-01"): 100_000.0, pd.Timestamp("2024-01-01"): 121_000.0})

    metrics = compute_metrics(equity, pd.DataFrame(), pd.Series(dtype=float))

    dias = (pd.Timestamp("2024-01-01") - pd.Timestamp("2022-01-01")).days
    n_anios = dias / 365.25
    cagr_esperado = ((121_000.0 / 100_000.0) ** (1 / n_anios) - 1) * 100

    assert metrics["retorno_total_pct"] == pytest.approx(21.0)
    assert metrics["cagr_pct"] == pytest.approx(cagr_esperado)


def test_sharpe_and_sortino_match_manual_calculation():
    retornos = np.array([0.01, 0.02, -0.01, 0.02, 0.01])
    equity_vals = np.insert(100_000.0 * np.cumprod(1 + retornos), 0, 100_000.0)
    fechas = pd.bdate_range("2024-01-02", periods=len(equity_vals))
    equity = pd.Series(equity_vals, index=fechas)

    metrics = compute_metrics(equity, pd.DataFrame(), pd.Series(dtype=float), risk_free_rate_annual=0.0)

    media = retornos.mean()
    desvio = retornos.std(ddof=1)
    sharpe_esperado = media / desvio * math.sqrt(252)
    downside = np.clip(retornos, None, 0)
    downside_std = math.sqrt((downside**2).mean())
    sortino_esperado = media / downside_std * math.sqrt(252)

    assert metrics["sharpe"] == pytest.approx(sharpe_esperado)
    assert metrics["sortino"] == pytest.approx(sortino_esperado)


def test_sharpe_is_nan_with_a_single_return_observation():
    # con un solo retorno diario, el desvío estándar (ddof=1) queda indefinido -> Sharpe NaN, no
    # división por cero ni un número gigante por ruido de punto flotante.
    equity = pd.Series([100_000.0, 101_000.0], index=pd.bdate_range("2024-01-01", periods=2))

    metrics = compute_metrics(equity, pd.DataFrame(), pd.Series(dtype=float))

    assert math.isnan(metrics["sharpe"])


def test_max_drawdown_and_duration_closed_form():
    fechas = pd.date_range("2024-01-01", periods=6, freq="D")
    equity = pd.Series([100.0, 120.0, 90.0, 80.0, 110.0, 130.0], index=fechas)

    metrics = compute_metrics(equity, pd.DataFrame(), pd.Series(dtype=float))

    assert metrics["max_drawdown_pct"] == pytest.approx((80.0 / 120.0 - 1) * 100)
    assert metrics["max_drawdown_duracion_dias"] == 4  # del pico (día 2) a la recuperación (día 6)


def test_trade_based_metrics_win_rate_profit_factor_expectancy():
    trades = pd.DataFrame(
        {
            "lado": ["COMPRA", "VENTA", "COMPRA", "VENTA", "VENTA", "VENTA"],
            "pnl_realizado": [None, 100.0, None, -50.0, 200.0, -100.0],
        }
    )
    equity = pd.Series([100_000.0, 100_150.0], index=pd.bdate_range("2024-01-01", periods=2))

    metrics = compute_metrics(equity, trades, pd.Series(dtype=float))

    assert metrics["numero_operaciones"] == 4
    assert metrics["win_rate_pct"] == pytest.approx(50.0)
    assert metrics["profit_factor"] == pytest.approx(2.0)  # 300 ganado / 150 perdido
    assert metrics["avg_win"] == pytest.approx(150.0)
    assert metrics["avg_loss"] == pytest.approx(-75.0)
    assert metrics["expectancy"] == pytest.approx(37.5)  # 0.5*150 + 0.5*(-75)
    assert metrics["mejor_operacion"] == pytest.approx(200.0)
    assert metrics["peor_operacion"] == pytest.approx(-100.0)


def test_trade_metrics_nan_and_no_crash_with_no_closed_trades():
    equity = pd.Series([100_000.0, 100_100.0], index=pd.bdate_range("2024-01-01", periods=2))
    metrics = compute_metrics(equity, pd.DataFrame(), pd.Series(dtype=float))

    assert metrics["numero_operaciones"] == 0
    assert math.isnan(metrics["win_rate_pct"])
    assert math.isnan(metrics["expectancy"])


def test_buy_and_hold_equity_buys_once_and_never_sells():
    fechas = pd.bdate_range("2024-01-01", periods=5)
    price_df = pd.DataFrame(
        {
            "fecha": fechas,
            "apertura": [100.0] * 5,
            "maximo": [100.0] * 5,
            "minimo": [100.0] * 5,
            "cierre": [100.0, 105.0, 95.0, 110.0, 120.0],
            "variacion": [0.0] * 5,
        }
    )
    cost_model = CostModel(comision_pct=0, derechos_mercado_pct=0, slippage_pct=0)

    equity = compute_buy_and_hold_equity(price_df, 100_000, cost_model, fechas[0], fechas[-1])

    assert equity.iloc[0] == pytest.approx(100_000.0)  # 1000 unidades @100, cash=0
    assert equity.iloc[-1] == pytest.approx(120_000.0)  # nunca vende, se valoriza al último cierre


def test_buy_and_hold_equity_applies_cost_model_to_entry():
    fechas = pd.bdate_range("2024-01-01", periods=2)
    price_df = pd.DataFrame(
        {
            "fecha": fechas,
            "apertura": [100.0, 100.0],
            "maximo": [100.0, 100.0],
            "minimo": [100.0, 100.0],
            "cierre": [100.0, 100.0],
            "variacion": [0.0, 0.0],
        }
    )
    cost_model = CostModel(comision_pct=1.0, derechos_mercado_pct=0.0, slippage_pct=0.0)

    equity = compute_buy_and_hold_equity(price_df, 10_000, cost_model, fechas[0], fechas[-1])

    costo_por_unidad = 100.0 * 1.01
    cantidad_esperada = int(10_000 // costo_por_unidad)
    assert cantidad_esperada == 99
    cash_restante = 10_000 - cantidad_esperada * costo_por_unidad
    assert equity.iloc[0] == pytest.approx(cantidad_esperada * 100.0 + cash_restante)


def test_buy_and_hold_equity_empty_when_no_data_in_range():
    price_df = pd.DataFrame(columns=["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"])
    equity = compute_buy_and_hold_equity(price_df, 100_000, CostModel(), pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-05"))
    assert equity.empty
