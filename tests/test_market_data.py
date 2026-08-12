from datetime import date

import pandas as pd

import iol_bot.price_cache as price_cache_module
from iol_bot.market_data import _to_daily_bars, get_historical_prices_cached
from iol_bot.price_cache import load_cache, save_cache


def test_to_daily_bars_collapses_intraday_ticks_into_one_row_per_day():
    # Reproduce el patrón real de IOL: días viejos con una sola fila, el día más reciente con
    # varios ticks intradía.
    df = pd.DataFrame(
        {
            "fecha": pd.to_datetime(
                [
                    "2024-01-01T17:00:00",
                    "2024-01-02T17:00:00",
                    "2024-01-03T10:00:00",
                    "2024-01-03T12:00:00",
                    "2024-01-03T16:59:00",
                ]
            ),
            "apertura": [100, 101, 103, 103, 103],
            "maximo": [102, 103, 106, 108, 107],
            "minimo": [99, 100, 102, 102, 101],
            "cierre": [101, 103, 105, 107, 104],
            "variacion": [1.0, 2.0, 2.0, 4.0, 1.0],
        }
    )

    result = _to_daily_bars(df)

    assert len(result) == 3  # 3 días calendario, no 5 filas
    dia3 = result[result["fecha"] == pd.Timestamp("2024-01-03")].iloc[0]
    assert dia3["apertura"] == 103  # primer tick del día
    assert dia3["maximo"] == 108  # máximo entre los 3 ticks
    assert dia3["minimo"] == 101  # mínimo entre los 3 ticks
    assert dia3["cierre"] == 104  # último tick del día
    assert dia3["variacion"] == 1.0  # último tick del día


def test_to_daily_bars_empty_df_returns_empty():
    df = pd.DataFrame(columns=["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"])
    result = _to_daily_bars(df)
    assert result.empty


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


def test_no_cache_triggers_full_fetch_and_saves_it(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    client = FakeClient(raw_response=[{"fechaHora": "2024-01-01T00:00:00", "ultimoPrecio": 100}])

    df = get_historical_prices_cached(client, "GGAL", mercado="bCBA", today=date(2024, 1, 2))

    assert len(client.calls) == 1
    assert len(df) == 1
    assert not load_cache("GGAL", "bCBA").empty


def test_cache_fresh_with_hoy_precio_skips_api_call_and_updates_last_row(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    hoy = date(2024, 3, 10)
    save_cache("GGAL", "bCBA", _cached_df(["2024-03-07", "2024-03-08"], [100.0, 105.0]))
    client = FakeClient()

    df = get_historical_prices_cached(client, "GGAL", mercado="bCBA", hoy_precio=110.0, today=hoy)

    assert client.calls == []  # no debe llamar a la API
    assert len(df) == 3  # se agregó la barra de HOY
    assert df.iloc[-1]["fecha"] == pd.Timestamp(hoy)
    assert df.iloc[-1]["cierre"] == 110.0
    # el cache en disco también quedó actualizado
    assert len(load_cache("GGAL", "bCBA")) == 3


def test_repeated_updates_same_day_track_max_min_and_replace_close(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    hoy = date(2024, 3, 10)
    save_cache("GGAL", "bCBA", _cached_df(["2024-03-08"], [100.0]))
    client = FakeClient()

    get_historical_prices_cached(client, "GGAL", mercado="bCBA", hoy_precio=110.0, today=hoy)
    df = get_historical_prices_cached(client, "GGAL", mercado="bCBA", hoy_precio=105.0, today=hoy)

    assert client.calls == []
    assert len(df) == 2  # sigue siendo una sola fila para HOY, no una por cada actualización
    fila_hoy = df.iloc[-1]
    assert fila_hoy["cierre"] == 105.0  # el cierre es el último precio visto
    assert fila_hoy["maximo"] == 110.0  # el máximo del día se mantiene
    assert fila_hoy["minimo"] == 105.0


def test_stale_cache_triggers_full_refetch(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    hoy = date(2024, 3, 10)
    # último dato del cache tiene más de CACHE_STALE_DAYS (5) sin actualizarse
    save_cache("GGAL", "bCBA", _cached_df(["2024-03-01"], [100.0]))
    client = FakeClient(raw_response=[{"fechaHora": "2024-03-10T00:00:00", "ultimoPrecio": 120}])

    df = get_historical_prices_cached(client, "GGAL", mercado="bCBA", hoy_precio=999.0, today=hoy)

    assert len(client.calls) == 1  # se ignoró hoy_precio y se refrescó todo
    assert df.iloc[-1]["cierre"] == 120


def test_missing_today_price_without_hoy_precio_fetches_short_window(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    hoy = date(2024, 3, 10)
    save_cache("GGAL", "bCBA", _cached_df(["2024-03-08"], [100.0]))
    client = FakeClient(raw_response=[{"fechaHora": "2024-03-10T00:00:00", "ultimoPrecio": 120}])

    df = get_historical_prices_cached(client, "GGAL", mercado="bCBA", hoy_precio=None, today=hoy)

    assert len(client.calls) == 1
    # pidió una ventana corta, no los 180 días completos por defecto
    fecha_desde = client.calls[0]["fecha_desde"]
    assert fecha_desde > "2024-01-01"
    assert df.iloc[-1]["cierre"] == 120


def test_cache_already_up_to_date_without_hoy_precio_makes_no_call(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    hoy = date(2024, 3, 10)
    save_cache("GGAL", "bCBA", _cached_df(["2024-03-10"], [100.0]))
    client = FakeClient()

    df = get_historical_prices_cached(client, "GGAL", mercado="bCBA", hoy_precio=None, today=hoy)

    assert client.calls == []
    assert len(df) == 1


def test_old_rows_get_trimmed_to_dias_window(tmp_path, monkeypatch):
    monkeypatch.setattr(price_cache_module, "CACHE_DIR", tmp_path)
    hoy = date(2024, 6, 1)
    fechas_viejas = ["2023-01-01", "2023-06-01"]  # muy fuera de la ventana de `dias`
    save_cache("GGAL", "bCBA", _cached_df(fechas_viejas + ["2024-05-31"], [50.0, 60.0, 100.0]))
    client = FakeClient()

    df = get_historical_prices_cached(client, "GGAL", mercado="bCBA", dias=30, hoy_precio=110.0, today=hoy)

    assert (df["fecha"] < pd.Timestamp(hoy) - pd.Timedelta(days=30)).sum() == 0
