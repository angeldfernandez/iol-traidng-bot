import pandas as pd


def sma(series, window):
    return series.rolling(window=window, min_periods=window).mean()


def ema(series, window):
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(series, window=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    result = 100 - (100 / (1 + rs))
    return result.fillna(100)


def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def bollinger_bands(series, window=20, num_std=2):
    mid = sma(series, window)
    std = series.rolling(window=window, min_periods=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower})


def atr(df, window=14):
    """Average True Range. `df` necesita columnas maximo, minimo, cierre (mismo formato que
    devuelve market_data.get_historical_prices). True range de cada rueda = el mayor entre
    (máximo-mínimo), |máximo-cierre_anterior| y |mínimo-cierre_anterior|."""
    maximo, minimo, cierre = df["maximo"], df["minimo"], df["cierre"]
    cierre_prev = cierre.shift(1)
    true_range = pd.concat(
        [maximo - minimo, (maximo - cierre_prev).abs(), (minimo - cierre_prev).abs()], axis=1
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()
