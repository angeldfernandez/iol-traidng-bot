import numpy as np
import pandas as pd
import pytest

from iol_bot.features import compute_features


def _price_df(n, start=100.0, daily_return=0.0, seed=None):
    """DataFrame sintético en el formato de market_data.get_historical_prices_cached, con
    crecimiento geométrico constante (o ruido reproducible si se pasa `seed`)."""
    fechas = pd.bdate_range("2024-01-01", periods=n)
    if seed is not None:
        rng = np.random.default_rng(seed)
        retornos = rng.normal(daily_return, 0.01, size=n)
    else:
        retornos = np.full(n, daily_return)
    cierres = start * np.cumprod(1 + retornos)
    return pd.DataFrame(
        {
            "fecha": fechas,
            "apertura": cierres,
            "maximo": cierres * 1.01,
            "minimo": cierres * 0.99,
            "cierre": cierres,
            "variacion": retornos * 100,
        }
    )


def test_compute_features_empty_price_df_returns_all_nan():
    features = compute_features(pd.DataFrame(columns=["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"]))
    assert features  # no está vacío el dict
    assert all(pd.isna(v) for v in features.values())


def test_compute_features_never_raises_on_short_history():
    price_df = _price_df(3)
    features = compute_features(price_df)
    # con solo 3 ruedas, casi todo debería ser NaN, pero no debe explotar.
    assert pd.isna(features["mom_60d"])
    assert pd.isna(features["sharpe_60d"])
    assert pd.isna(features["dist_sma_100"])


def test_momentum_matches_manual_calculation():
    price_df = _price_df(70, start=100.0, daily_return=0.01)
    features = compute_features(price_df)
    esperado_5d = price_df["cierre"].iloc[-1] / price_df["cierre"].iloc[-6] - 1
    assert features["mom_5d"] == pytest.approx(esperado_5d)
    assert features["mom_20d"] > 0  # crecimiento constante positivo


def test_price_range_position_in_range_bounds():
    price_df = _price_df(130, start=100.0, daily_return=0.005)
    features = compute_features(price_df)
    assert 0.0 <= features["position_in_range"] <= 1.0
    # con crecimiento constante, el máximo de la ventana es el de la rueda más reciente (maximo =
    # cierre*1.01 cada día), así que el cierre actual queda muy cerca del máximo del rango.
    assert features["dist_to_high"] == pytest.approx(0.0, abs=0.02)
    assert features["position_in_range"] > 0.95


def test_relative_strength_nan_without_benchmark():
    price_df = _price_df(70, daily_return=0.01)
    features = compute_features(price_df, benchmark_df=None)
    assert pd.isna(features["rs_excess_20d"])
    assert pd.isna(features["rs_trend_60d"])


def test_relative_strength_excess_return_vs_benchmark():
    candidato = _price_df(70, start=100.0, daily_return=0.02)
    benchmark = _price_df(70, start=100.0, daily_return=0.005)
    features = compute_features(candidato, benchmark_df=benchmark)

    ret_candidato = candidato["cierre"].iloc[-1] / candidato["cierre"].iloc[-21] - 1
    ret_benchmark = benchmark["cierre"].iloc[-1] / benchmark["cierre"].iloc[-21] - 1
    assert features["rs_excess_20d"] == pytest.approx(ret_candidato - ret_benchmark, rel=1e-6)
    assert features["rs_excess_20d"] > 0  # el candidato rinde más que el benchmark


def test_volume_features_use_history_and_relative_volume():
    price_df = _price_df(30)
    fechas_previas = pd.bdate_range("2024-01-01", periods=60)
    volume_history = pd.Series(np.full(60, 1000.0), index=fechas_previas)

    features = compute_features(price_df, volume_history=volume_history, today_volumen_nominal=2000.0)

    assert features["avg_volume_20d"] == pytest.approx(1000.0)
    assert features["avg_volume_60d"] == pytest.approx(1000.0)
    assert features["relative_volume"] == pytest.approx(2.0)
    assert features["liquidity_today"] == pytest.approx(2000.0)


def test_volume_features_nan_without_enough_history():
    price_df = _price_df(30)
    volume_history = pd.Series([1000.0, 1100.0])  # solo 2 días, no alcanza para 20d/60d

    features = compute_features(price_df, volume_history=volume_history, today_volumen_nominal=500.0)

    assert pd.isna(features["avg_volume_20d"])
    assert pd.isna(features["avg_volume_60d"])
    assert pd.isna(features["relative_volume"])
    assert features["liquidity_today"] == pytest.approx(500.0)


def test_risk_adjusted_features_nan_when_no_negative_returns():
    # retornos siempre positivos pero variables (no constantes) para que el desvío estándar no
    # sea cero — así sharpe es calculable pero sortino queda NaN por no haber downside.
    n = 70
    retornos = np.tile([0.005, 0.02, 0.008, 0.015], n // 4 + 1)[:n]
    fechas = pd.bdate_range("2024-01-01", periods=n)
    cierres = 100.0 * np.cumprod(1 + retornos)
    price_df = pd.DataFrame(
        {
            "fecha": fechas,
            "apertura": cierres,
            "maximo": cierres * 1.01,
            "minimo": cierres * 0.99,
            "cierre": cierres,
            "variacion": retornos * 100,
        }
    )

    features = compute_features(price_df)
    assert pd.notna(features["sharpe_60d"])
    assert pd.isna(features["sortino_60d"])  # sin retornos negativos, desvío downside indefinido
