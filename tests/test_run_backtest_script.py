import math

from scripts.run_backtest import _filas_comparacion, _fmt


def test_fmt_formats_normal_values():
    assert _fmt(12.345, "{:.2f}%") == "12.35%"
    assert _fmt(3, "{:.0f}") == "3"
    assert _fmt(1234.5, "${:,.2f}") == "$1,234.50"


def test_fmt_handles_nan_and_inf_and_none():
    assert _fmt(float("nan"), "{:.2f}") == "N/D"
    assert _fmt(float("inf"), "{:.2f}") == "inf"
    assert _fmt(None, "{:.2f}") == "N/D"


def test_filas_comparacion_pairs_estrategia_and_benchmark_by_key():
    metrics_estrategia = {"retorno_total_pct": 10.0, "sharpe": 1.5, "numero_operaciones": 4}
    metrics_benchmark = {"retorno_total_pct": 8.0, "sharpe": float("nan"), "numero_operaciones": 0}

    filas = _filas_comparacion(metrics_estrategia, metrics_benchmark)
    filas_por_label = {label: (val_e, val_b) for label, val_e, val_b in filas}

    assert filas_por_label["Retorno total"] == ("10.00%", "8.00%")
    assert filas_por_label["Sharpe"] == ("1.50", "N/D")


def test_filas_comparacion_missing_key_shows_nd():
    filas = _filas_comparacion({}, {})
    assert all(val_e == "N/D" and val_b == "N/D" for _label, val_e, val_b in filas)
    assert not any(math.isnan(0) for _ in filas)  # solo un sanity check de que no explota
