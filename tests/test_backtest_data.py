from datetime import date

import pandas as pd

import iol_bot.backtest_cache as backtest_cache_module
from iol_bot.backtest_cache import load_cache, save_cache
from iol_bot.backtest_data import get_historical_prices_backtest_cached


class FakeClient:
    def __init__(self, raw_response=None):
        self.calls = []
        self._raw_response = raw_response if raw_response is not None else []

    def serie_historica(self, **kwargs):
        self.calls.append(kwargs)
        return self._raw_response


def _cached_df(fechas, cierres):
    return pd.DataFrame(
        {
            "fecha": pd.to_datetime(fechas),
            "apertura": cierres,
            "maximo": cierres,
            "minimo": cierres,
            "cierre": cierres,
            "variacion": [0.0] * len(fechas),
        }
    )


def test_cache_covering_full_range_skips_api_call(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest_cache_module, "BACKTEST_CACHE_DIR", tmp_path)
    save_cache("GGAL", "bCBA", _cached_df(["2024-01-01", "2024-06-01", "2024-12-31"], [100.0, 110.0, 120.0]))
    client = FakeClient()

    df = get_historical_prices_backtest_cached(client, "GGAL", date(2024, 1, 1), date(2024, 12, 31), mercado="bCBA")

    assert client.calls == []
    assert len(df) == 3


def test_cache_not_covering_range_fetches_and_merges_without_shrinking(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest_cache_module, "BACKTEST_CACHE_DIR", tmp_path)
    # cache previo: rango ANCHO (una corrida anterior con más historia)
    save_cache("GGAL", "bCBA", _cached_df(["2020-01-01", "2024-01-01"], [50.0, 100.0]))
    client = FakeClient(
        raw_response=[
            {"fechaHora": "2024-06-01T00:00:00", "ultimoPrecio": 115},
            {"fechaHora": "2024-12-31T00:00:00", "ultimoPrecio": 120},
        ]
    )

    # corrida nueva: rango ANGOSTO, no cubierto por el cache existente (falta 2024-12-31)
    df = get_historical_prices_backtest_cached(client, "GGAL", date(2024, 6, 1), date(2024, 12, 31), mercado="bCBA")

    assert len(client.calls) == 1
    assert len(df) == 2  # el resultado devuelto respeta el rango pedido

    # pero el cache en disco NO perdió la historia vieja de 2020 (nunca se achica)
    cache_completo = load_cache("GGAL", "bCBA")
    assert pd.Timestamp("2020-01-01") in set(cache_completo["fecha"])


def test_coverage_mismatch_logs_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(backtest_cache_module, "BACKTEST_CACHE_DIR", tmp_path)
    # IOL devuelve menos historia de la pedida (empieza después de fecha_desde)
    client = FakeClient(raw_response=[{"fechaHora": "2024-06-01T00:00:00", "ultimoPrecio": 100}])

    with caplog.at_level("WARNING"):
        df = get_historical_prices_backtest_cached(client, "GGAL", date(2020, 1, 1), date(2024, 12, 31), mercado="bCBA")

    assert len(df) == 1
    assert any("limitando el rango" in r.message for r in caplog.records)


def test_empty_response_returns_empty_df_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest_cache_module, "BACKTEST_CACHE_DIR", tmp_path)
    client = FakeClient(raw_response=[])

    df = get_historical_prices_backtest_cached(client, "GGAL", date(2024, 1, 1), date(2024, 12, 31), mercado="bCBA")

    assert df.empty
