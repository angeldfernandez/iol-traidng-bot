"""Normaliza los features crudos de iol_bot/features.py a subscores 0-100 y los combina en un
score_total, usando los pesos/grupos de config/scoring.yaml (iol_bot/scoring_config.py).

La normalización es por percentil, TRANSVERSAL al pool de símbolos evaluados ese día (no contra
un valor absoluto fijo) — es decir, el score de un símbolo depende de cómo está posicionado ese
día frente a los demás candidatos, no de un umbral hardcodeado. Esto es deliberado (sección 10 del
prompt maestro pide un sistema de ranking relativo, no reglas absolutas) pero tiene una
consecuencia real: con pools chicos el percentil queda "grumoso" (poca resolución) — ranking.py
loggea un warning cuando el pool elegible es chico."""
import pandas as pd


def compute_subscores(features_df, cfg):
    """features_df: DataFrame indexado por símbolo, columnas = features crudos de
    iol_bot/features.py (el pool "elegible" del día — ya filtrado por calidad).

    cfg: ScoringConfig (iol_bot/scoring_config.py).

    Devuelve un DataFrame con las mismas filas, más una columna "{grupo}_score" por cada grupo de
    cfg.groups y una columna final "score_total" (0-100). Todo NaN-safe: nunca lanza excepción,
    un símbolo con datos insuficientes simplemente queda con subscores/score_total en NaN."""
    resultado = features_df.copy()

    if resultado.empty:
        for grupo in cfg.groups:
            resultado[f"{grupo}_score"] = pd.Series(dtype=float)
        resultado["score_total"] = pd.Series(dtype=float)
        return resultado

    componentes = pd.DataFrame(index=resultado.index)
    for grupo, features in cfg.groups.items():
        for feature in features:
            if feature not in resultado.columns:
                continue
            pct = resultado[feature].rank(pct=True, na_option="keep") * 100
            if cfg.is_lower_better(feature):
                pct = 100 - pct
            componentes[feature] = pct

    grupo_score_cols = {}
    for grupo, features in cfg.groups.items():
        presentes = [f for f in features if f in componentes.columns]
        col = f"{grupo}_score"
        grupo_score_cols[grupo] = col
        resultado[col] = componentes[presentes].mean(axis=1, skipna=True) if presentes else float("nan")

    pesos = pd.Series(cfg.weights)
    grupo_scores = resultado[[grupo_score_cols[g] for g in pesos.index if g in grupo_score_cols]]
    grupo_scores = grupo_scores.rename(columns={grupo_score_cols[g]: g for g in pesos.index if g in grupo_score_cols})

    disponible = grupo_scores.notna()
    peso_efectivo = disponible.mul(pesos, axis=1)
    peso_total_fila = peso_efectivo.sum(axis=1)
    suma_ponderada = grupo_scores.fillna(0).mul(pesos, axis=1).sum(axis=1)

    resultado["score_total"] = (suma_ponderada / peso_total_fila).where(peso_total_fila > 0)

    return resultado
