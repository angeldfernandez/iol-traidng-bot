"""Fetch de históricos para backtesting: a diferencia de market_data.get_historical_prices_cached
(pensado para el bot en vivo, ventana corta que se trimea siempre), acá se pide un rango de fechas
explícito y el cache resultante (backtest_cache/) solo crece — nunca se achica entre corridas."""
import logging

import pandas as pd

from iol_bot import backtest_cache
from iol_bot.market_data import DATE_FMT, _fetch_and_parse_serie, _merge_series

logger = logging.getLogger("iol_bot.backtest_data")


def get_historical_prices_backtest(client, simbolo, fecha_desde, fecha_hasta, mercado="bCBA", ajustada="sinAjustar"):
    """Pull directo sin cache, para un rango de fechas explícito (fecha_desde/fecha_hasta: date o
    string YYYY-MM-DD)."""
    desde_str = fecha_desde if isinstance(fecha_desde, str) else fecha_desde.strftime(DATE_FMT)
    hasta_str = fecha_hasta if isinstance(fecha_hasta, str) else fecha_hasta.strftime(DATE_FMT)
    return _fetch_and_parse_serie(client, simbolo, desde_str, hasta_str, mercado, ajustada)


def get_historical_prices_backtest_cached(client, simbolo, fecha_desde, fecha_hasta, mercado="bCBA", ajustada="sinAjustar"):
    """Como get_historical_prices_backtest, pero cachea en backtest_cache/ (nunca se trimea). Si el
    cache ya cubre [fecha_desde, fecha_hasta] por completo, no llama a la API. Si no, pide el rango
    pedido y lo UNE con lo que ya había cacheado (nunca sobreescribe/achica) — así una corrida con
    rango angosto no le come historia a una corrida anterior con rango más ancho."""
    desde_ts = pd.Timestamp(fecha_desde)
    hasta_ts = pd.Timestamp(fecha_hasta)

    cached = backtest_cache.load_cache(simbolo, mercado)
    cubre = not cached.empty and cached["fecha"].min() <= desde_ts and cached["fecha"].max() >= hasta_ts

    if not cubre:
        nuevo = get_historical_prices_backtest(client, simbolo, fecha_desde, fecha_hasta, mercado, ajustada)
        combinado = _merge_series(cached, nuevo)
        if not combinado.empty:
            backtest_cache.save_cache(simbolo, mercado, combinado)
        cached = combinado

    if cached.empty:
        return cached

    resultado = cached[(cached["fecha"] >= desde_ts) & (cached["fecha"] <= hasta_ts)].reset_index(drop=True)

    if not resultado.empty and resultado["fecha"].min() > desde_ts:
        logger.warning(
            "%s: se pidió histórico desde %s pero la serie recibida arranca en %s — IOL puede estar "
            "limitando el rango histórico disponible; verificar contra la cuenta real antes de "
            "confiar en un backtest de ventana larga.",
            simbolo, desde_ts.date(), resultado["fecha"].min().date(),
        )

    return resultado
