import logging
from datetime import date, datetime, timedelta

import pandas as pd

from iol_bot.price_cache import load_cache, save_cache

logger = logging.getLogger("iol_bot.market_data")

DATE_FMT = "%Y-%m-%d"

# Si el cache no se actualiza hace más de esto, se descarta y se pide la serie completa de nuevo
# (cubre fines de semana largos sin que haga falta lógica de feriados).
CACHE_STALE_DAYS = 5


def get_historical_prices(client, simbolo, mercado="bCBA", dias=180, ajustada="sinAjustar"):
    """Trae la serie histórica de un símbolo y la devuelve como DataFrame ordenado por fecha.

    Columnas: fecha, apertura, maximo, minimo, cierre, variacion
    """
    fecha_hasta = datetime.now()
    fecha_desde = fecha_hasta - timedelta(days=dias)

    raw = client.serie_historica(
        simbolo=simbolo,
        fecha_desde=fecha_desde.strftime(DATE_FMT),
        fecha_hasta=fecha_hasta.strftime(DATE_FMT),
        mercado=mercado,
        ajustada=ajustada,
    )

    if not raw:
        logger.warning("Serie histórica vacía para %s", simbolo)
        return pd.DataFrame(columns=["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"])

    df = pd.DataFrame(raw)
    df = df.rename(
        columns={
            "fechaHora": "fecha",
            "apertura": "apertura",
            "maximo": "maximo",
            "minimo": "minimo",
            "ultimoPrecio": "cierre",
            "variacion": "variacion",
        }
    )
    # IOL devuelve fechas en formatos mixtos (con y sin milisegundos) dentro de la misma serie.
    df["fecha"] = pd.to_datetime(df["fecha"], format="ISO8601")
    df = df.sort_values("fecha").reset_index(drop=True)

    cols = [c for c in ["fecha", "apertura", "maximo", "minimo", "cierre", "variacion"] if c in df.columns]
    df = df[cols]

    return _to_daily_bars(df)


def _to_daily_bars(df):
    """IOL mezcla granularidades en la misma serie: la mayoría de los días vienen como una sola
    fila (cierre diario), pero el día más reciente puede venir con decenas o cientos de ticks
    intradía. Sin agrupar por día, ese ruido intradía domina las ventanas de SMA/RSI. Se agrupa
    por fecha calendario y se queda con apertura del primer tick, máximo/mínimo del día, y cierre
    del último tick (o el único valor si el día trae una sola fila)."""
    if df.empty:
        return df

    agg_rules = {
        "apertura": "first",
        "maximo": "max",
        "minimo": "min",
        "cierre": "last",
        "variacion": "last",
    }
    agg_rules = {col: rule for col, rule in agg_rules.items() if col in df.columns}

    grouped = df.groupby(df["fecha"].dt.date).agg(**{col: (col, rule) for col, rule in agg_rules.items()})
    grouped.index = pd.to_datetime(grouped.index)
    grouped.index.name = "fecha"
    return grouped.reset_index()


def get_historical_prices_cached(client, simbolo, mercado="bCBA", dias=180, hoy_precio=None, today=None):
    """Como get_historical_prices, pero cachea localmente en cache/ para no volver a pedir la
    serie completa en cada ciclo del día.

    - Si el cache está vacío o desactualizado (más de CACHE_STALE_DAYS sin novedades), se hace un
      fetch completo a la API (como antes) y se guarda en cache.
    - Si `hoy_precio` viene provisto (por ejemplo, el último precio que ya trajo gratis el scan de
      paneles) y el cache está al día hasta ayer o antes, se actualiza/agrega la barra de HOY con
      ese precio SIN llamar a serie_historica — de acá sale la mayor parte del ahorro de llamadas.
    - Si no hay `hoy_precio` y al cache le falta el día de hoy, se pide solo una ventana corta
      reciente (no los `dias` completos) para ponerlo al día.
    """
    today = today or date.today()
    cached = load_cache(simbolo, mercado)

    cache_utilizable = not cached.empty and (today - cached["fecha"].max().date()).days <= CACHE_STALE_DAYS
    if not cache_utilizable:
        df = get_historical_prices(client, simbolo, mercado=mercado, dias=dias)
        if not df.empty:
            save_cache(simbolo, mercado, df)
        return df

    ultima_fecha_cache = cached["fecha"].max().date()

    if hoy_precio is not None:
        actualizado = _apply_today_price(cached, ultima_fecha_cache, today, hoy_precio)
        actualizado = _trim_to_window(actualizado, today, dias)
        save_cache(simbolo, mercado, actualizado)
        return actualizado

    if ultima_fecha_cache >= today:
        return cached

    df_reciente = get_historical_prices(client, simbolo, mercado=mercado, dias=CACHE_STALE_DAYS + 2)
    combinado = _merge_series(cached, df_reciente)
    combinado = _trim_to_window(combinado, today, dias)
    save_cache(simbolo, mercado, combinado)
    return combinado


def _apply_today_price(cached, ultima_fecha_cache, today, precio):
    hoy_ts = pd.Timestamp(today)

    if ultima_fecha_cache == today:
        fila_previa = cached.iloc[-1]
        nueva_fila = {
            "fecha": hoy_ts,
            "apertura": fila_previa["apertura"],
            "maximo": max(fila_previa["maximo"], precio),
            "minimo": min(fila_previa["minimo"], precio),
            "cierre": precio,
            "variacion": fila_previa.get("variacion"),
        }
        base = cached.iloc[:-1]
    else:
        nueva_fila = {"fecha": hoy_ts, "apertura": precio, "maximo": precio, "minimo": precio, "cierre": precio, "variacion": None}
        base = cached

    return pd.concat([base, pd.DataFrame([nueva_fila])], ignore_index=True)


def _merge_series(cached, nuevo):
    if nuevo.empty:
        return cached
    combinado = pd.concat([cached, nuevo], ignore_index=True)
    combinado = combinado.drop_duplicates(subset="fecha", keep="last")
    combinado = combinado.sort_values("fecha").reset_index(drop=True)
    return combinado


def _trim_to_window(df, today, dias):
    corte = pd.Timestamp(today) - timedelta(days=dias)
    return df[df["fecha"] >= corte].reset_index(drop=True)
