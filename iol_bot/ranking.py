"""Orquesta el embudo de selección (sección 6/11 del prompt maestro): scan de candidatos por
liquidez -> filtro de calidad -> features -> scoring -> TOP N -> persistencia diaria.

`build_daily_ranking` devuelve la watchlist final en el mismo formato que ya devolvía
market_scanner.scan_market ([{"simbolo","mercado","ultimo_precio"}]), pensado como reemplazo
directo de esa llamada en main.py y scripts/paper_trade.py — la estrategia, el risk management y
la ejecución de órdenes no cambian en esta fase, solo cambia CÓMO se arma la lista de símbolos a
evaluar.

Persistencia: un CSV por día en rankings/{YYYY-MM-DD}.csv (gitignored, igual que cache/ y logs/),
con TODOS los candidatos evaluados ese día (elegibles o no, con motivo) — nunca se descarta un
símbolo en silencio. El archivo de HOY se reescribe en cada ciclo (igual que la barra de "hoy" en
price_cache) — es un valor vivo hasta el cierre del mercado, no una foto fija de fin de día."""
import logging
from datetime import date, timedelta

import pandas as pd

from iol_bot.client import IOLApiError
from iol_bot.config import PROJECT_ROOT
from iol_bot.features import compute_features
from iol_bot.market_data import get_historical_prices_cached
from iol_bot.market_scanner import scan_market_candidates
from iol_bot.scoring import compute_subscores
from iol_bot.scoring_config import ScoringConfig

logger = logging.getLogger("iol_bot.ranking")

RANKINGS_DIR = PROJECT_ROOT / "rankings"


def _evaluar_calidad(price_df, item, scoring_cfg):
    if price_df is None or price_df.empty:
        return False, "sin datos históricos"
    if len(price_df) < scoring_cfg.min_history_days:
        return False, f"historia insuficiente ({len(price_df)} < {scoring_cfg.min_history_days} ruedas)"

    ultimo_precio = item.get("ultimo_precio") or 0
    if ultimo_precio < scoring_cfg.min_price:
        return False, f"precio por debajo del mínimo ({ultimo_precio} < {scoring_cfg.min_price})"

    volumen_nominal = item.get("volumen_nominal") or 0
    if volumen_nominal < scoring_cfg.min_avg_volume:
        return False, f"volumen nominal insuficiente ({volumen_nominal:.0f} < {scoring_cfg.min_avg_volume:.0f})"

    return True, "ok"


def _sort_ranked(df):
    """Orden determinístico del pool elegible: score_total desc, después volume_score desc como
    desempate, después símbolo asc para que el resultado no dependa del orden de iteración."""
    elegibles = df[df["eligible"]].copy()
    if "volume_score" not in elegibles.columns:
        elegibles["volume_score"] = float("nan")
    return elegibles.sort_values(
        by=["score_total", "volume_score", "simbolo"], ascending=[False, False, True], na_position="last"
    )


def _load_volume_history_matrix(before_day, lookback_days=60):
    """Matriz fecha x símbolo de volumen_nominal, reconstruida a partir de rankings ya
    persistidos (estrictamente anteriores a `before_day`). Se usa para calcular avg_volume_20d/60d
    en features.py — no depende de que price_cache tenga columna de volumen (no la tiene)."""
    if not RANKINGS_DIR.exists():
        return pd.DataFrame()

    cutoff = before_day - timedelta(days=lookback_days * 2)
    filas_por_dia = {}
    for path in sorted(RANKINGS_DIR.glob("*.csv")):
        try:
            dia = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if dia >= before_day or dia < cutoff:
            continue
        try:
            df = pd.read_csv(path)
        except (OSError, pd.errors.ParserError):
            continue
        if "simbolo" not in df.columns or "volumen_nominal" not in df.columns:
            continue
        filas_por_dia[dia] = dict(zip(df["simbolo"], df["volumen_nominal"]))

    if not filas_por_dia:
        return pd.DataFrame()

    matriz = pd.DataFrame.from_dict(filas_por_dia, orient="index").sort_index()
    return matriz.tail(lookback_days)


def build_daily_ranking(client, config, scoring_cfg=None, today=None):
    """Pipeline completo. `config` es el Config operativo del bot (paneles, etc.), `scoring_cfg`
    un ScoringConfig (se carga de config/scoring.yaml si no se pasa uno)."""
    scoring_cfg = scoring_cfg or ScoringConfig.load()
    today = today or date.today()

    candidatos = scan_market_candidates(
        client, paneles=config.paneles, top_n=scoring_cfg.candidate_pool_size, pais="argentina"
    )
    candidatos = [c for c in candidatos if c["simbolo"] != scoring_cfg.benchmark_simbolo]

    if not candidatos:
        logger.warning("build_daily_ranking: el scan de candidatos no devolvió símbolos")
        save_ranking(today, pd.DataFrame(columns=["simbolo", "mercado", "ultimo_precio", "volumen_nominal", "eligible", "motivo"]))
        return []

    benchmark_df = None
    try:
        benchmark_df = get_historical_prices_cached(
            client, scoring_cfg.benchmark_simbolo, mercado=scoring_cfg.benchmark_mercado
        )
        if benchmark_df.empty:
            benchmark_df = None
    except IOLApiError as exc:
        logger.warning(
            "No se pudo traer el benchmark %s: %s — la fuerza relativa quedará en NaN hoy",
            scoring_cfg.benchmark_simbolo, exc,
        )

    volumen_matriz = _load_volume_history_matrix(today)

    filas = []
    for item in candidatos:
        simbolo, mercado = item["simbolo"], item["mercado"]
        try:
            price_df = get_historical_prices_cached(client, simbolo, mercado=mercado, hoy_precio=item.get("ultimo_precio"))
        except IOLApiError as exc:
            logger.warning("Error de datos para %s, queda fuera del ranking de hoy: %s", simbolo, exc)
            filas.append(
                {
                    "simbolo": simbolo,
                    "mercado": mercado,
                    "ultimo_precio": item.get("ultimo_precio"),
                    "volumen_nominal": item.get("volumen_nominal"),
                    "eligible": False,
                    "motivo": f"error de datos: {exc}",
                }
            )
            continue

        eligible, motivo = _evaluar_calidad(price_df, item, scoring_cfg)
        volume_history = volumen_matriz[simbolo].dropna() if simbolo in volumen_matriz.columns else None
        features = compute_features(
            price_df, benchmark_df=benchmark_df, volume_history=volume_history,
            today_volumen_nominal=item.get("volumen_nominal"),
        )

        fila = {
            "simbolo": simbolo,
            "mercado": mercado,
            "ultimo_precio": item.get("ultimo_precio"),
            "volumen_nominal": item.get("volumen_nominal"),
            "eligible": eligible,
            "motivo": motivo,
        }
        fila.update(features)
        filas.append(fila)

    df = pd.DataFrame(filas)
    elegibles_df = df[df["eligible"]].set_index("simbolo")

    if not elegibles_df.empty:
        feature_cols = [c for grupo in scoring_cfg.groups.values() for c in grupo if c in elegibles_df.columns]
        scored = compute_subscores(elegibles_df[feature_cols], scoring_cfg)
        score_cols = [c for c in scored.columns if c not in feature_cols]
        scored_por_simbolo = scored[score_cols].reset_index().rename(columns={"index": "simbolo"})
        df = df.merge(scored_por_simbolo, on="simbolo", how="left")
    else:
        # Nadie pasó el filtro de calidad hoy: igual hace falta score_total/"{grupo}_score" para
        # que _sort_ranked (y el resto de las columnas esperadas del CSV) no exploten más abajo.
        for grupo in scoring_cfg.groups:
            df[f"{grupo}_score"] = float("nan")
        df["score_total"] = float("nan")

    if len(elegibles_df) < scoring_cfg.top_n_final:
        logger.warning(
            "Pool elegible (%d símbolos) por debajo del TOP final configurado (%d) — el scoring "
            "por percentil pierde resolución con pools chicos",
            len(elegibles_df), scoring_cfg.top_n_final,
        )

    ranked = _sort_ranked(df).reset_index(drop=True)
    if not ranked.empty:
        ranked = ranked.assign(rank=range(1, len(ranked) + 1))
        df = df.merge(ranked[["simbolo", "rank"]], on="simbolo", how="left")

    save_ranking(today, df)

    top_df = ranked.head(scoring_cfg.top_n_final)
    return [
        {"simbolo": row.simbolo, "mercado": row.mercado, "ultimo_precio": row.ultimo_precio}
        for row in top_df.itertuples()
    ]


def save_ranking(day, df):
    RANKINGS_DIR.mkdir(parents=True, exist_ok=True)
    path = RANKINGS_DIR / f"{day.isoformat()}.csv"
    tmp_path = path.with_suffix(".tmp")
    df.to_csv(tmp_path, index=False)
    tmp_path.replace(path)


def load_ranking(day):
    path = RANKINGS_DIR / f"{day.isoformat()}.csv"
    if not path.exists():
        return None
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.ParserError):
        return None


def get_current_ranking(today=None):
    return load_ranking(today or date.today())


def get_previous_ranking(today=None):
    """El archivo más reciente ESTRICTAMENTE anterior a `today` (salta fines de semana/feriados
    automáticamente, ya que solo se guarda ranking los días que corrió el bot)."""
    today = today or date.today()
    if not RANKINGS_DIR.exists():
        return None

    dias_anteriores = []
    for path in RANKINGS_DIR.glob("*.csv"):
        try:
            dia = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if dia < today:
            dias_anteriores.append(dia)

    if not dias_anteriores:
        return None
    return load_ranking(max(dias_anteriores))


def diff_rankings(current_df, previous_df, top_n=50):
    """Entradas/salidas/evolución del TOP N entre dos rankings (sección 11). Si `previous_df` es
    None (primer día con datos, o gap sin ranking previo), todo el TOP N actual se reporta como
    "entries" y no hay "exits" ni "evolution" — no se trata como error."""
    if current_df is None or current_df.empty:
        return {"entries": [], "exits": [], "evolution": []}

    top_actual = _sort_ranked(current_df).head(top_n).reset_index(drop=True)
    top_actual["rank"] = range(1, len(top_actual) + 1)

    if previous_df is None or previous_df.empty:
        return {"entries": top_actual["simbolo"].tolist(), "exits": [], "evolution": []}

    top_previo = _sort_ranked(previous_df).head(top_n).reset_index(drop=True)
    top_previo["rank"] = range(1, len(top_previo) + 1)

    simbolos_actuales = set(top_actual["simbolo"])
    simbolos_previos = set(top_previo["simbolo"])

    entries = sorted(simbolos_actuales - simbolos_previos)
    exits = sorted(simbolos_previos - simbolos_actuales)

    actual_idx = top_actual.set_index("simbolo")
    previo_idx = top_previo.set_index("simbolo")

    evolution = []
    for simbolo in sorted(simbolos_actuales & simbolos_previos):
        score_prev = float(previo_idx.loc[simbolo, "score_total"])
        score_curr = float(actual_idx.loc[simbolo, "score_total"])
        rank_prev = int(previo_idx.loc[simbolo, "rank"])
        rank_curr = int(actual_idx.loc[simbolo, "rank"])
        evolution.append(
            {
                "simbolo": simbolo,
                "score_prev": score_prev,
                "score_curr": score_curr,
                "rank_prev": rank_prev,
                "rank_curr": rank_curr,
                "delta_rank": rank_prev - rank_curr,
                "delta_score": score_curr - score_prev,
            }
        )

    return {"entries": entries, "exits": exits, "evolution": evolution}


def get_symbol_history(simbolo, start=None, end=None):
    """Evolución del score de un símbolo a lo largo del tiempo, leyendo todos los rankings
    persistidos en el rango [start, end] (ambos inclusive; None = sin límite)."""
    columnas_vacio = ["fecha", "simbolo", "eligible", "score_total", "rank"]
    if not RANKINGS_DIR.exists():
        return pd.DataFrame(columns=columnas_vacio)

    filas = []
    for path in sorted(RANKINGS_DIR.glob("*.csv")):
        try:
            dia = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start and dia < start:
            continue
        if end and dia > end:
            continue

        df = load_ranking(dia)
        if df is None or "simbolo" not in df.columns:
            continue
        fila = df[df["simbolo"] == simbolo]
        if fila.empty:
            continue
        registro = fila.iloc[0].to_dict()
        registro["fecha"] = dia
        filas.append(registro)

    if not filas:
        return pd.DataFrame(columns=columnas_vacio)
    return pd.DataFrame(filas).sort_values("fecha").reset_index(drop=True)
