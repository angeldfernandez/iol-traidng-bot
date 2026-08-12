import pandas as pd

import iol_bot.price_cache as price_cache_module
from iol_bot.price_cache import load_cache, save_cache


def test_load_cache_missing_file_returns_empty_df(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    df = load_cache("GGAL", "bCBA")
    assert df.empty
    assert list(df.columns) == ["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"]


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    df = pd.DataFrame(
        {
            "fecha": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "apertura": [100.0, 101.0],
            "maximo": [102.0, 103.0],
            "minimo": [99.0, 100.0],
            "cierre": [101.0, 102.0],
            "variacion": [1.0, 1.0],
        }
    )
    save_cache("GGAL", "bCBA", df)

    loaded = load_cache("GGAL", "bCBA")
    assert len(loaded) == 2
    assert loaded["cierre"].tolist() == [101.0, 102.0]
    assert loaded["fecha"].iloc[0] == pd.Timestamp("2024-01-01")


def test_cache_files_are_separate_per_simbolo_y_mercado(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    df = pd.DataFrame(
        {
            "fecha": pd.to_datetime(["2024-01-01"]),
            "apertura": [1.0],
            "maximo": [1.0],
            "minimo": [1.0],
            "cierre": [1.0],
            "variacion": [0.0],
        }
    )
    save_cache("GGAL", "bCBA", df)
    assert load_cache("YPFD", "bCBA").empty
