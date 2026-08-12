import pandas as pd

from iol_bot.indicators import atr, bollinger_bands, ema, macd, rsi, sma


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5])
    result = sma(s, window=3)
    assert result.iloc[2] == 2.0
    assert result.iloc[3] == 3.0
    assert result.iloc[4] == 4.0
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])


def test_ema_matches_pandas_ewm():
    s = pd.Series(range(1, 21), dtype=float)
    result = ema(s, window=5)
    expected = s.ewm(span=5, adjust=False, min_periods=5).mean()
    pd.testing.assert_series_equal(result, expected)


def test_rsi_all_gains_is_100():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], dtype=float)
    result = rsi(s, window=14)
    assert result.iloc[-1] == 100


def test_rsi_all_losses_is_0():
    s = pd.Series(list(range(16, 0, -1)), dtype=float)
    result = rsi(s, window=14)
    assert result.iloc[-1] == 0


def test_macd_returns_expected_columns():
    s = pd.Series(range(1, 41), dtype=float)
    result = macd(s)
    assert list(result.columns) == ["macd", "signal", "histogram"]
    assert (result["histogram"].dropna() == (result["macd"] - result["signal"]).dropna()).all()


def test_bollinger_bands_upper_above_lower():
    s = pd.Series([10, 11, 9, 12, 8, 13, 7, 14, 6, 15, 5, 16, 4, 17, 3, 18, 2, 19, 1, 20], dtype=float)
    result = bollinger_bands(s, window=10)
    valid = result.dropna()
    assert (valid["upper"] >= valid["mid"]).all()
    assert (valid["mid"] >= valid["lower"]).all()


def test_atr_basic_true_range_and_gap():
    df = pd.DataFrame(
        {
            "maximo": [12, 13, 20],
            "minimo": [8, 9, 15],
            "cierre": [10, 11, 18],  # fila 2 es un gap up respecto al cierre previo (11 -> 18)
        }
    )
    result = atr(df, window=2)

    # fila 0: sin cierre previo, TR = máximo-mínimo = 4. Con min_periods=2, todavía no hay ATR.
    assert pd.isna(result.iloc[0])
    # fila 1: TR = max(13-9, |13-10|, |9-10|) = 4. ATR = mean(TR0=4, TR1=4) = 4.
    assert result.iloc[1] == 4.0
    # fila 2: TR = max(20-15, |20-11|, |15-11|) = 9 (domina el gap, no el rango del día).
    # ATR = mean(TR1=4, TR2=9) = 6.5
    assert result.iloc[2] == 6.5


def test_atr_nan_before_window_fills():
    df = pd.DataFrame({"maximo": [10, 11, 12, 13], "minimo": [9, 10, 11, 12], "cierre": [9.5, 10.5, 11.5, 12.5]})
    result = atr(df, window=14)
    assert result.isna().all()
