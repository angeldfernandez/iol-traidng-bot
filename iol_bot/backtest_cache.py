"""Cache de precios históricos para backtesting — separado por completo de cache/ (price_cache.py),
que está pensado para el bot en vivo y se trimea siempre a una ventana corta (ver backtest_data.py
para el porqué). Mismo formato/patrón de archivo (tmp + replace) que price_cache.py, pero esta
cache NUNCA se trimea: solo crece (backtest_data.py la une con lo nuevo, nunca la reemplaza)."""
import pandas as pd

from iol_bot.config import PROJECT_ROOT

BACKTEST_CACHE_DIR = PROJECT_ROOT / "backtest_cache"

_COLUMNS = ["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"]


def _cache_path(simbolo, mercado):
    return BACKTEST_CACHE_DIR / f"{mercado}_{simbolo}.csv"


def load_cache(simbolo, mercado):
    path = _cache_path(simbolo, mercado)
    if not path.exists():
        return pd.DataFrame(columns=_COLUMNS)
    df = pd.read_csv(path, parse_dates=["fecha"])
    return df


def save_cache(simbolo, mercado, df):
    BACKTEST_CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(simbolo, mercado)
    tmp_path = path.with_suffix(".tmp")
    df.to_csv(tmp_path, index=False)
    tmp_path.replace(path)
