"""Feature engineering para el motor de scoring/ranking (iol_bot/ranking.py).

Todas las funciones son puras (no hacen I/O) y NUNCA levantan una excepción por falta de
historia: si una ventana no tiene suficientes datos, el feature correspondiente queda en NaN
(igual que sma/rsi en indicators.py) — scoring.py sabe ignorar NaN al calcular subscores.

Nota sobre ventanas: el cache de precios (market_data.get_historical_prices_cached) guarda por
defecto ~180 días corridos, que en el mercado argentino (lunes a viernes, con feriados) son unas
120-125 ruedas. Por eso las ventanas de este módulo llegan como máximo a 60-120 ruedas — ventanas
más largas (SMA200, momentum 252d, máximo/mínimo de 52 semanas) quedan pendientes para cuando se
amplíe esa ventana de cache (ver README, sección "Qué falta").
"""
import numpy as np
import pandas as pd

from iol_bot.indicators import atr, macd, rsi, sma

TRADING_DAYS_YEAR = 252


def _last_valid(series):
    return float(series.iloc[-1]) if not series.empty and pd.notna(series.iloc[-1]) else float("nan")


def _momentum_features(close):
    features = {}
    for n in (5, 10, 20, 60):
        if len(close) > n and close.iloc[-1 - n] not in (0, None) and pd.notna(close.iloc[-1 - n]):
            features[f"mom_{n}d"] = float(close.iloc[-1] / close.iloc[-1 - n] - 1)
        else:
            features[f"mom_{n}d"] = float("nan")
    return features


def _trend_features(close):
    features = {}
    for w in (20, 50, 100):
        sma_w = _last_valid(sma(close, w))
        if pd.notna(sma_w) and sma_w != 0:
            features[f"dist_sma_{w}"] = float(close.iloc[-1] / sma_w - 1)
        else:
            features[f"dist_sma_{w}"] = float("nan")

    if len(close) >= 20:
        ultimos = close.iloc[-20:].to_numpy(dtype=float)
        media = ultimos.mean()
        if media != 0:
            pendiente = np.polyfit(range(20), ultimos, 1)[0]
            features["slope_20d"] = float(pendiente / media)
        else:
            features["slope_20d"] = float("nan")
    else:
        features["slope_20d"] = float("nan")
    return features


def _technical_momentum_features(close):
    features = {}
    features["rsi_14"] = _last_valid(rsi(close, 14))
    macd_df = macd(close)
    features["macd_hist"] = _last_valid(macd_df["histogram"])
    return features


def _volatility_features(df):
    close = df["cierre"]
    features = {}

    atr_series = atr(df, 14)
    atr_last = _last_valid(atr_series)
    features["atr_pct"] = float(atr_last / close.iloc[-1]) if pd.notna(atr_last) and close.iloc[-1] else float("nan")

    log_ret = np.log(close / close.shift(1))
    for w in (20, 60):
        if len(close) > w:
            vol = log_ret.iloc[-w:].std()
            features[f"vol_{w}d"] = float(vol * np.sqrt(TRADING_DAYS_YEAR)) if pd.notna(vol) else float("nan")
        else:
            features[f"vol_{w}d"] = float("nan")

    ventana = close.iloc[-60:] if len(close) >= 2 else close
    if len(ventana) >= 2:
        drawdown = ventana / ventana.cummax() - 1
        features["max_drawdown_60d"] = float(drawdown.min())
    else:
        features["max_drawdown_60d"] = float("nan")

    return features


def _price_range_features(df):
    features = {}
    ventana = df.iloc[-120:] if len(df) >= 2 else df
    if len(ventana) < 2:
        return {"dist_to_high": float("nan"), "dist_to_low": float("nan"), "position_in_range": float("nan")}

    high = float(ventana["maximo"].max())
    low = float(ventana["minimo"].min())
    close_actual = float(df["cierre"].iloc[-1])

    features["dist_to_high"] = float((high - close_actual) / high) if high else float("nan")
    features["dist_to_low"] = float((close_actual - low) / low) if low else float("nan")
    features["position_in_range"] = float(np.clip((close_actual - low) / (high - low), 0, 1)) if high != low else float("nan")
    return features


def _risk_adjusted_features(close):
    features = {}
    returns = close.pct_change().dropna()
    muestra = returns.iloc[-60:] if len(returns) >= 20 else returns

    if len(muestra) >= 20:
        media, desvio = muestra.mean(), muestra.std()
        features["sharpe_60d"] = float(media / desvio * np.sqrt(TRADING_DAYS_YEAR)) if desvio else float("nan")

        negativos = muestra[muestra < 0]
        desvio_downside = negativos.std() if len(negativos) >= 2 else float("nan")
        features["sortino_60d"] = (
            float(media / desvio_downside * np.sqrt(TRADING_DAYS_YEAR))
            if pd.notna(desvio_downside) and desvio_downside
            else float("nan")
        )

        precios_muestra = close.iloc[-len(muestra) - 1 :]
        retorno_total = float(precios_muestra.iloc[-1] / precios_muestra.iloc[0] - 1)
        drawdown = precios_muestra / precios_muestra.cummax() - 1
        max_dd = abs(float(drawdown.min()))
        features["calmar_60d"] = float(retorno_total / max_dd) if max_dd else float("nan")
    else:
        features["sharpe_60d"] = float("nan")
        features["sortino_60d"] = float("nan")
        features["calmar_60d"] = float("nan")

    return features


def _relative_strength_features(close, benchmark_df):
    keys = ["rs_excess_5d", "rs_excess_20d", "rs_excess_60d", "rs_trend_60d"]
    if benchmark_df is None or benchmark_df.empty:
        return {k: float("nan") for k in keys}

    candidato = pd.DataFrame({"fecha": close.index, "cierre_candidato": close.values})
    merged = candidato.merge(
        benchmark_df[["fecha", "cierre"]].rename(columns={"cierre": "cierre_benchmark"}), on="fecha", how="inner"
    ).sort_values("fecha")

    if len(merged) < 6:
        return {k: float("nan") for k in keys}

    features = {}
    for n in (5, 20, 60):
        if len(merged) > n:
            ret_candidato = merged["cierre_candidato"].iloc[-1] / merged["cierre_candidato"].iloc[-1 - n] - 1
            ret_benchmark = merged["cierre_benchmark"].iloc[-1] / merged["cierre_benchmark"].iloc[-1 - n] - 1
            features[f"rs_excess_{n}d"] = float(ret_candidato - ret_benchmark)
        else:
            features[f"rs_excess_{n}d"] = float("nan")

    if len(merged) >= 60:
        ratio = (merged["cierre_candidato"] / merged["cierre_benchmark"]).iloc[-60:].to_numpy(dtype=float)
        media = ratio.mean()
        features["rs_trend_60d"] = float(np.polyfit(range(60), ratio, 1)[0] / media) if media else float("nan")
    else:
        features["rs_trend_60d"] = float("nan")

    return features


def _volume_features(volume_history, today_volumen_nominal):
    features = {"liquidity_today": float(today_volumen_nominal) if today_volumen_nominal is not None else float("nan")}

    for w in (20, 60):
        if volume_history is not None and len(volume_history) >= w:
            features[f"avg_volume_{w}d"] = float(volume_history.iloc[-w:].mean())
        else:
            features[f"avg_volume_{w}d"] = float("nan")

    avg_20 = features["avg_volume_20d"]
    if pd.notna(avg_20) and avg_20 and today_volumen_nominal is not None:
        features["relative_volume"] = float(today_volumen_nominal / avg_20)
    else:
        features["relative_volume"] = float("nan")

    return features


def compute_features(price_df, benchmark_df=None, volume_history=None, today_volumen_nominal=None):
    """price_df: columnas fecha, apertura, maximo, minimo, cierre, variacion, ordenado por fecha
    ascendente (formato de market_data.get_historical_prices_cached).

    benchmark_df: precio del símbolo de referencia (mismo formato) para fuerza relativa, o None.

    volume_history: pd.Series de volumen nominal diario del propio símbolo (histórico, sin
    incluir hoy), indexada por fecha — la arma iol_bot/ranking.py a partir de rankings pasados.

    today_volumen_nominal: volumen*precio de hoy (gratis del scan de paneles), o None.

    Devuelve un dict plano de features crudos (sin normalizar) — NaN cuando no hay historia
    suficiente. Nunca lanza excepción."""
    if price_df is None or price_df.empty:
        keys = (
            [f"mom_{n}d" for n in (5, 10, 20, 60)]
            + [f"dist_sma_{w}" for w in (20, 50, 100)]
            + ["slope_20d", "rsi_14", "macd_hist", "atr_pct", "vol_20d", "vol_60d", "max_drawdown_60d"]
            + ["dist_to_high", "dist_to_low", "position_in_range"]
            + ["sharpe_60d", "sortino_60d", "calmar_60d"]
            + ["rs_excess_5d", "rs_excess_20d", "rs_excess_60d", "rs_trend_60d"]
            + ["liquidity_today", "avg_volume_20d", "avg_volume_60d", "relative_volume"]
        )
        return {k: float("nan") for k in keys}

    close = price_df.set_index("fecha")["cierre"]

    features = {}
    features.update(_momentum_features(close))
    features.update(_trend_features(close))
    features.update(_technical_momentum_features(close))
    features.update(_volatility_features(price_df))
    features.update(_price_range_features(price_df))
    features.update(_risk_adjusted_features(close))
    features.update(_relative_strength_features(close, benchmark_df))
    features.update(_volume_features(volume_history, today_volumen_nominal))
    return features
