import pandas as pd

from iol_bot.config import PROJECT_ROOT

CACHE_DIR = PROJECT_ROOT / "cache"

_COLUMNS = ["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"]


def _cache_path(simbolo, mercado):
    return CACHE_DIR / f"{mercado}_{simbolo}.csv"


def load_cache(simbolo, mercado):
    path = _cache_path(simbolo, mercado)
    if not path.exists():
        return pd.DataFrame(columns=_COLUMNS)
    df = pd.read_csv(path, parse_dates=["fecha"])
    return df


def save_cache(simbolo, mercado, df):
    CACHE_DIR.mkdir(exist_ok=True)
    path = _cache_path(simbolo, mercado)
    tmp_path = path.with_suffix(".tmp")
    df.to_csv(tmp_path, index=False)
    tmp_path.replace(path)
