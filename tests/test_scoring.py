import math

import pandas as pd
import pytest

from iol_bot.scoring import compute_subscores
from iol_bot.scoring_config import ScoringConfig


def _cfg(groups, weights, directionality=None):
    return ScoringConfig(
        candidate_pool_size=150,
        top_n_final=50,
        min_history_days=30,
        min_avg_volume=0,
        min_price=0,
        benchmark_simbolo="BENCH",
        benchmark_mercado="bCBA",
        groups=groups,
        weights=weights,
        directionality_lower_is_better=set(directionality or []),
    )


def test_percentile_rank_and_group_mean():
    features_df = pd.DataFrame(
        {"f1": [1, 2, 3], "f2": [3, 1, 2]}, index=["A", "B", "C"]
    )
    cfg = _cfg(groups={"g1": ["f1", "f2"]}, weights={"g1": 1.0})

    result = compute_subscores(features_df, cfg)

    # f1: A=1(pct 33.33), B=2(pct 66.67), C=3(pct 100)
    # f2: A=3(pct 100), B=1(pct 33.33), C=2(pct 66.67)
    assert result.loc["A", "g1_score"] == pytest.approx((100 / 3 + 100) / 2, rel=1e-3)
    assert result.loc["B", "g1_score"] == pytest.approx((200 / 3 + 100 / 3) / 2, rel=1e-3)
    assert result.loc["C", "g1_score"] == pytest.approx((100 + 200 / 3) / 2, rel=1e-3)
    # con un solo grupo de peso 1.0, score_total == g1_score
    pd.testing.assert_series_equal(result["score_total"], result["g1_score"], check_names=False)


def test_directionality_flip_inverts_percentile():
    features_df = pd.DataFrame({"f1": [1, 2, 3]}, index=["A", "B", "C"])
    cfg_normal = _cfg(groups={"g1": ["f1"]}, weights={"g1": 1.0})
    cfg_invertido = _cfg(groups={"g1": ["f1"]}, weights={"g1": 1.0}, directionality=["f1"])

    normal = compute_subscores(features_df, cfg_normal)
    invertido = compute_subscores(features_df, cfg_invertido)

    assert normal.loc["A", "score_total"] < normal.loc["C", "score_total"]
    assert invertido.loc["A", "score_total"] > invertido.loc["C", "score_total"]
    assert normal.loc["A", "score_total"] == pytest.approx(100 - invertido.loc["A", "score_total"])


def test_group_score_nan_when_all_members_nan():
    features_df = pd.DataFrame({"f1": [1.0, 2.0, float("nan")]}, index=["A", "B", "C"])
    cfg = _cfg(groups={"g1": ["f1"]}, weights={"g1": 1.0})

    result = compute_subscores(features_df, cfg)

    assert math.isnan(result.loc["C", "g1_score"])
    assert math.isnan(result.loc["C", "score_total"])
    assert not math.isnan(result.loc["A", "score_total"])


def test_score_total_renormalizes_when_one_group_missing():
    features_df = pd.DataFrame(
        {"f1": [1.0, 2.0, 3.0], "f2": [1.0, float("nan"), 3.0]}, index=["A", "B", "C"]
    )
    cfg = _cfg(groups={"g1": ["f1"], "g2": ["f2"]}, weights={"g1": 0.6, "g2": 0.4})

    result = compute_subscores(features_df, cfg)

    # B no tiene f2 -> g2_score es NaN -> score_total de B debe ser exactamente su g1_score
    # (se renormaliza sobre el peso disponible, no se le imputa 0 al grupo faltante).
    assert result.loc["B", "score_total"] == pytest.approx(result.loc["B", "g1_score"])
    # A y C sí tienen ambos grupos -> score_total es el promedio ponderado de los dos.
    esperado_a = 0.6 * result.loc["A", "g1_score"] + 0.4 * result.loc["A", "g2_score"]
    assert result.loc["A", "score_total"] == pytest.approx(esperado_a)


def test_compute_subscores_empty_dataframe_does_not_raise():
    features_df = pd.DataFrame(columns=["f1"])
    cfg = _cfg(groups={"g1": ["f1"]}, weights={"g1": 1.0})

    result = compute_subscores(features_df, cfg)

    assert result.empty
    assert "score_total" in result.columns
    assert "g1_score" in result.columns


def test_scoring_config_load_rejects_weights_not_summing_to_one(tmp_path):
    bad_yaml = tmp_path / "scoring.yaml"
    bad_yaml.write_text(
        "funnel: {candidate_pool_size: 10, top_n_final: 5}\n"
        "quality_filters: {min_history_days: 1, min_avg_volume: 0, min_price: 0}\n"
        "relative_strength: {benchmark_simbolo: SPY, benchmark_mercado: bCBA}\n"
        "groups: {g1: [f1]}\n"
        "weights: {g1: 0.5}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="deben sumar 1.0"):
        ScoringConfig.load(bad_yaml)


def test_scoring_config_load_rejects_unknown_group_in_weights(tmp_path):
    bad_yaml = tmp_path / "scoring.yaml"
    bad_yaml.write_text(
        "funnel: {candidate_pool_size: 10, top_n_final: 5}\n"
        "quality_filters: {min_history_days: 1, min_avg_volume: 0, min_price: 0}\n"
        "relative_strength: {benchmark_simbolo: SPY, benchmark_mercado: bCBA}\n"
        "groups: {g1: [f1]}\n"
        "weights: {g_inexistente: 1.0}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inexistentes"):
        ScoringConfig.load(bad_yaml)


def test_scoring_config_load_reads_real_default_file():
    cfg = ScoringConfig.load()
    assert cfg.top_n_final == 50
    assert cfg.candidate_pool_size >= cfg.top_n_final
    assert sum(cfg.weights.values()) == pytest.approx(1.0)
