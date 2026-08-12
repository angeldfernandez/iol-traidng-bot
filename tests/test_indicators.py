import pandas as pd

from iol_bot.indicators import bollinger_bands, ema, macd, rsi, sma


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
