from datetime import date

import numpy as np
import pandas as pd
import pytest

import iol_bot.ranking as ranking_module
from iol_bot.client import IOLApiError
from iol_bot.ranking import (
    build_daily_ranking,
    diff_rankings,
    get_previous_ranking,
    get_symbol_history,
    load_ranking,
    save_ranking,
)
from iol_bot.scoring_config import ScoringConfig


class FakeConfig:
    paneles = ["merval", "cedears"]


def _cfg(candidate_pool_size=10, top_n_final=3, min_history_days=20, min_avg_volume=0, min_price=0):
    return ScoringConfig(
        candidate_pool_size=candidate_pool_size,
        top_n_final=top_n_final,
        min_history_days=min_history_days,
        min_avg_volume=min_avg_volume,
        min_price=min_price,
        benchmark_simbolo="SPY",
        benchmark_mercado="bCBA",
        groups={"momentum": ["mom_5d", "mom_10d"], "volume": ["liquidity_today"]},
        weights={"momentum": 0.7, "volume": 0.3},
        directionality_lower_is_better=set(),
    )


def _price_df(n=40, start=100.0, daily_return=0.01):
    fechas = pd.bdate_range("2024-01-01", periods=n)
    cierres = start * np.cumprod(np.full(n, 1 + daily_return))
    return pd.DataFrame(
        {
            "fecha": fechas,
            "apertura": cierres,
            "maximo": cierres * 1.01,
            "minimo": cierres * 0.99,
            "cierre": cierres,
            "variacion": daily_return * 100,
        }
    )


def _candidato(simbolo, ultimo_precio=100.0, volumen_nominal=1_000_000.0):
    return {"simbolo": simbolo, "mercado": "bCBA", "ultimo_precio": ultimo_precio, "volumen_nominal": volumen_nominal}


@pytest.fixture(autouse=True)
def isolated_rankings_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ranking_module, "RANKINGS_DIR", tmp_path)
    return tmp_path


def test_build_daily_ranking_returns_scan_market_shape(monkeypatch):
    candidatos = [_candidato("AAPL"), _candidato("GGAL"), _candidato("YPFD")]
    monkeypatch.setattr(ranking_module, "scan_market_candidates", lambda client, paneles, top_n, pais: candidatos)
    monkeypatch.setattr(
        ranking_module, "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: _price_df(),
    )

    watchlist = build_daily_ranking(object(), FakeConfig(), scoring_cfg=_cfg(), today=date(2024, 6, 3))

    assert watchlist
    for item in watchlist:
        assert set(item.keys()) == {"simbolo", "mercado", "ultimo_precio"}

    ranking_hoy = load_ranking(date(2024, 6, 3))
    assert ranking_hoy is not None
    assert set(ranking_hoy["simbolo"]) == {"AAPL", "GGAL", "YPFD"}


def test_build_daily_ranking_excludes_benchmark_symbol(monkeypatch):
    candidatos = [_candidato("SPY"), _candidato("GGAL")]
    monkeypatch.setattr(ranking_module, "scan_market_candidates", lambda client, paneles, top_n, pais: candidatos)
    monkeypatch.setattr(
        ranking_module, "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: _price_df(),
    )

    build_daily_ranking(object(), FakeConfig(), scoring_cfg=_cfg(), today=date(2024, 6, 3))

    ranking_hoy = load_ranking(date(2024, 6, 3))
    assert "SPY" not in set(ranking_hoy["simbolo"])
    assert "GGAL" in set(ranking_hoy["simbolo"])


def test_quality_filter_marks_ineligible_instead_of_dropping(monkeypatch):
    candidatos = [_candidato("AAPL"), _candidato("NUEVA")]

    def fake_prices(client, simbolo, mercado=None, hoy_precio=None):
        if simbolo == "NUEVA":
            return _price_df(n=3)  # historia insuficiente (< min_history_days)
        return _price_df(n=40)

    monkeypatch.setattr(ranking_module, "scan_market_candidates", lambda client, paneles, top_n, pais: candidatos)
    monkeypatch.setattr(ranking_module, "get_historical_prices_cached", fake_prices)

    build_daily_ranking(object(), FakeConfig(), scoring_cfg=_cfg(min_history_days=20), today=date(2024, 6, 3))

    ranking_hoy = load_ranking(date(2024, 6, 3))
    fila_nueva = ranking_hoy[ranking_hoy["simbolo"] == "NUEVA"].iloc[0]
    assert fila_nueva["eligible"] == False  # noqa: E712 (viene de CSV, comparar como bool/str es lo que importa)
    assert "historia insuficiente" in fila_nueva["motivo"]
    # AAPL sí pasa el filtro
    fila_aapl = ranking_hoy[ranking_hoy["simbolo"] == "AAPL"].iloc[0]
    assert bool(fila_aapl["eligible"])


def test_per_symbol_api_error_does_not_crash_whole_run(monkeypatch):
    candidatos = [_candidato("AAPL"), _candidato("ROTA")]

    def fake_prices(client, simbolo, mercado=None, hoy_precio=None):
        if simbolo == "ROTA":
            raise IOLApiError("500 error simulado")
        return _price_df()

    monkeypatch.setattr(ranking_module, "scan_market_candidates", lambda client, paneles, top_n, pais: candidatos)
    monkeypatch.setattr(ranking_module, "get_historical_prices_cached", fake_prices)

    watchlist = build_daily_ranking(object(), FakeConfig(), scoring_cfg=_cfg(), today=date(2024, 6, 3))

    assert any(item["simbolo"] == "AAPL" for item in watchlist)
    ranking_hoy = load_ranking(date(2024, 6, 3))
    fila_rota = ranking_hoy[ranking_hoy["simbolo"] == "ROTA"].iloc[0]
    assert not bool(fila_rota["eligible"])
    assert "error de datos" in fila_rota["motivo"]


def test_build_daily_ranking_with_zero_eligible_candidates_does_not_crash(monkeypatch):
    # Caso real: corrida antes de la apertura del mercado, volumen_nominal=0 para todos los
    # candidatos -> nadie pasa el filtro de calidad. score_total/"{grupo}_score" deben existir
    # igual (aunque vacíos) para que el ordenamiento no explote con un KeyError.
    candidatos = [_candidato("AAPL", volumen_nominal=0.0), _candidato("GGAL", volumen_nominal=0.0)]
    monkeypatch.setattr(ranking_module, "scan_market_candidates", lambda client, paneles, top_n, pais: candidatos)
    monkeypatch.setattr(
        ranking_module, "get_historical_prices_cached",
        lambda client, simbolo, mercado=None, hoy_precio=None: _price_df(),
    )

    watchlist = build_daily_ranking(
        object(), FakeConfig(), scoring_cfg=_cfg(min_avg_volume=1_000_000), today=date(2024, 6, 3)
    )

    assert watchlist == []
    ranking_hoy = load_ranking(date(2024, 6, 3))
    assert "score_total" in ranking_hoy.columns
    assert ranking_hoy["eligible"].sum() == 0


def test_save_and_load_ranking_roundtrip():
    df = pd.DataFrame({"simbolo": ["AAPL", "GGAL"], "score_total": [80.0, 60.0]})
    save_ranking(date(2024, 6, 3), df)

    cargado = load_ranking(date(2024, 6, 3))

    pd.testing.assert_frame_equal(cargado, df)
    assert load_ranking(date(2024, 6, 4)) is None


def test_get_previous_ranking_skips_gaps_and_none_on_first_run():
    assert get_previous_ranking(today=date(2024, 6, 5)) is None

    save_ranking(date(2024, 5, 31), pd.DataFrame({"simbolo": ["A"], "score_total": [50.0]}))  # viernes
    save_ranking(date(2024, 6, 3), pd.DataFrame({"simbolo": ["B"], "score_total": [60.0]}))  # lunes

    previo = get_previous_ranking(today=date(2024, 6, 5))  # miércoles, sin ranking del martes/feriado

    assert previo["simbolo"].tolist() == ["B"]  # el más reciente ANTERIOR a today, saltando el hueco


def test_diff_rankings_first_run_reports_all_as_entries():
    current = pd.DataFrame(
        {"simbolo": ["A", "B"], "eligible": [True, True], "score_total": [80.0, 70.0], "volume_score": [50.0, 50.0]}
    )
    diff = diff_rankings(current, previous_df=None, top_n=5)
    assert set(diff["entries"]) == {"A", "B"}
    assert diff["exits"] == []
    assert diff["evolution"] == []


def test_diff_rankings_entries_exits_and_evolution():
    previo = pd.DataFrame(
        {
            "simbolo": ["A", "B", "C"],
            "eligible": [True, True, True],
            "score_total": [90.0, 80.0, 70.0],
            "volume_score": [50.0, 50.0, 50.0],
        }
    )
    actual = pd.DataFrame(
        {
            "simbolo": ["A", "C", "D"],
            "eligible": [True, True, True],
            "score_total": [95.0, 60.0, 85.0],
            "volume_score": [50.0, 50.0, 50.0],
        }
    )

    diff = diff_rankings(actual, previo, top_n=5)

    assert diff["entries"] == ["D"]
    assert diff["exits"] == ["B"]
    evolucion_por_simbolo = {e["simbolo"]: e for e in diff["evolution"]}
    assert set(evolucion_por_simbolo) == {"A", "C"}
    # A: subió de rank 1 a rank 1 (se mantiene primero), score subió de 90 a 95.
    assert evolucion_por_simbolo["A"]["delta_score"] == pytest.approx(5.0)
    # C: bajó de rank 3 a rank 2 (mejoró de puesto) aunque el score bajó de 70 a 60 — posible
    # cuando cambia la composición del pool, no es una inconsistencia del diff.
    assert evolucion_por_simbolo["C"]["delta_score"] == pytest.approx(-10.0)


def test_get_symbol_history_across_multiple_files():
    save_ranking(date(2024, 6, 3), pd.DataFrame({"simbolo": ["A", "B"], "score_total": [80.0, 70.0]}))
    save_ranking(date(2024, 6, 4), pd.DataFrame({"simbolo": ["B"], "score_total": [72.0]}))  # A no está este día
    save_ranking(date(2024, 6, 5), pd.DataFrame({"simbolo": ["A", "B"], "score_total": [85.0, 74.0]}))

    historial_a = get_symbol_history("A")

    assert historial_a["fecha"].tolist() == [date(2024, 6, 3), date(2024, 6, 5)]
    assert historial_a["score_total"].tolist() == [80.0, 85.0]


def test_get_symbol_history_empty_when_never_present():
    save_ranking(date(2024, 6, 3), pd.DataFrame({"simbolo": ["A"], "score_total": [80.0]}))
    historial = get_symbol_history("ZZZ")
    assert historial.empty
