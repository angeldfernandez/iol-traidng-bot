"""Backtest de la estrategia (por defecto SmaCrossoverRsiStrategy) contra históricos reales de IOL,
con costos de transacción, sin look-ahead bias, comparado contra buy & hold de un benchmark.

Reusa la MISMA lógica de riesgo que corre en vivo/paper trading (apply_position_override,
RiskManager) — el objetivo es validar el comportamiento real del bot contra el pasado, no simular
algo distinto. Universo, rango de fechas, capital y costos se configuran en config/backtest.yaml
(nunca hardcodeados acá).

Necesita las credenciales reales de .env para autenticar contra IOL (igual que scripts/paper_trade.py)
aunque toda la data termine sirviéndose desde backtest_cache/ en corridas repetidas.

Uso: python -m scripts.run_backtest
"""
import json
import logging
import math
from dataclasses import asdict
from datetime import datetime

import pandas as pd

from iol_bot.auth import IOLAuth
from iol_bot.backtest_config import BacktestConfig
from iol_bot.backtest_data import get_historical_prices_backtest_cached
from iol_bot.backtest_engine import run_backtest
from iol_bot.backtest_metrics import compute_buy_and_hold_equity, compute_metrics
from iol_bot.client import IOLApiError, IOLClient
from iol_bot.config import PROJECT_ROOT, Config
from iol_bot.logging_config import setup_logging
from iol_bot.scoring_config import ScoringConfig
from iol_bot.strategy import SmaCrossoverRsiStrategy

logger = logging.getLogger("iol_bot.run_backtest")

BACKTESTS_DIR = PROJECT_ROOT / "backtests"

# (clave en el dict de métricas, etiqueta para el reporte, formato)
METRICAS_LABELS = [
    ("retorno_total_pct", "Retorno total", "{:.2f}%"),
    ("cagr_pct", "CAGR", "{:.2f}%"),
    ("retorno_anual_promedio_pct", "Retorno anual promedio", "{:.2f}%"),
    ("volatilidad_anualizada_pct", "Volatilidad anualizada", "{:.2f}%"),
    ("sharpe", "Sharpe", "{:.2f}"),
    ("sortino", "Sortino", "{:.2f}"),
    ("max_drawdown_pct", "Max drawdown", "{:.2f}%"),
    ("max_drawdown_duracion_dias", "Duración máx. drawdown (días)", "{:.0f}"),
    ("calmar", "Calmar", "{:.2f}"),
    ("numero_operaciones", "Número de operaciones", "{:.0f}"),
    ("win_rate_pct", "Win rate", "{:.2f}%"),
    ("profit_factor", "Profit factor", "{:.2f}"),
    ("avg_win", "Ganancia promedio", "${:,.2f}"),
    ("avg_loss", "Pérdida promedio", "${:,.2f}"),
    ("expectancy", "Expectancy (por operación)", "${:,.2f}"),
    ("mejor_operacion", "Mejor operación", "${:,.2f}"),
    ("peor_operacion", "Peor operación", "${:,.2f}"),
    ("turnover_anualizado", "Turnover anualizado", "{:.2f}x"),
    ("costos_totales", "Costos totales", "${:,.2f}"),
    ("exposicion_promedio_pct", "Exposición promedio", "{:.2f}%"),
]


def _fmt(valor, formato):
    if valor is None:
        return "N/D"
    if isinstance(valor, float):
        if math.isnan(valor):
            return "N/D"
        if math.isinf(valor):
            return "inf"
    return formato.format(valor)


def _filas_comparacion(metrics_estrategia, metrics_benchmark):
    return [
        (label, _fmt(metrics_estrategia.get(key), formato), _fmt(metrics_benchmark.get(key), formato))
        for key, label, formato in METRICAS_LABELS
    ]


def main():
    setup_logging()
    # RiskManager.reset_daily() loggea a nivel INFO — normal para el bot en vivo/paper (una vez por
    # día real), pero un backtest de años dispara esa misma línea una vez por día SIMULADO (cientos
    # de veces), inundando la salida. Subimos el piso a WARNING para este script puntual — los
    # warnings reales (ej. circuit breaker activado) se siguen viendo.
    logging.getLogger("iol_bot.risk").setLevel(logging.WARNING)

    config = Config.load()
    scoring_cfg = ScoringConfig.load()
    bt_cfg = BacktestConfig.load(
        default_risk_limits=config.risk,
        default_benchmark=(scoring_cfg.benchmark_simbolo, scoring_cfg.benchmark_mercado),
    )

    auth = IOLAuth(config.base_url, config.username, config.password)
    client = IOLClient(config.base_url, auth)
    strategy = SmaCrossoverRsiStrategy()

    logger.info(
        "Backtest iniciado | simbolos=%s rango=%s..%s capital=$%.2f",
        [item["simbolo"] for item in bt_cfg.simbolos],
        bt_cfg.fecha_desde,
        bt_cfg.fecha_hasta,
        bt_cfg.capital_inicial,
    )

    price_data = {}
    for item in bt_cfg.simbolos:
        try:
            price_data[item["simbolo"]] = get_historical_prices_backtest_cached(
                client, item["simbolo"], bt_cfg.fecha_desde, bt_cfg.fecha_hasta, item["mercado"]
            )
        except IOLApiError as exc:
            logger.error("No se pudo traer histórico de %s, se lo excluye del backtest: %s", item["simbolo"], exc)

    if not price_data:
        logger.error("No se pudo traer histórico de ningún símbolo del universo — abortando")
        return

    benchmark_df = get_historical_prices_backtest_cached(
        client, bt_cfg.benchmark_simbolo, bt_cfg.fecha_desde, bt_cfg.fecha_hasta, bt_cfg.benchmark_mercado
    )

    resultado = run_backtest(
        strategy, price_data, bt_cfg.fecha_desde, bt_cfg.fecha_hasta, bt_cfg.capital_inicial, bt_cfg.cost_model, bt_cfg.risk_limits
    )
    equity_benchmark = compute_buy_and_hold_equity(
        benchmark_df, bt_cfg.capital_inicial, bt_cfg.cost_model, bt_cfg.fecha_desde, bt_cfg.fecha_hasta
    )

    metrics_estrategia = compute_metrics(
        resultado.equity_curve, resultado.trades, resultado.exposure_curve, bt_cfg.risk_free_rate_annual
    )
    exposure_benchmark = pd.Series(1.0, index=equity_benchmark.index) if not equity_benchmark.empty else pd.Series(dtype=float)
    metrics_benchmark = compute_metrics(equity_benchmark, pd.DataFrame(), exposure_benchmark, bt_cfg.risk_free_rate_annual)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = BACKTESTS_DIR / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    curva = pd.DataFrame({"fecha": resultado.equity_curve.index, "equity_estrategia": resultado.equity_curve.values})
    if not equity_benchmark.empty:
        curva_bench = pd.DataFrame({"fecha": equity_benchmark.index, "equity_benchmark": equity_benchmark.values})
        curva = curva.merge(curva_bench, on="fecha", how="outer").sort_values("fecha")
    curva.to_csv(out_dir / "equity_curve.csv", index=False)
    resultado.trades.to_csv(out_dir / "trades.csv", index=False)

    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_id": run_id,
                "estrategia": metrics_estrategia,
                "benchmark": metrics_benchmark,
                "config": {
                    "simbolos": bt_cfg.simbolos,
                    "fecha_desde": str(bt_cfg.fecha_desde),
                    "fecha_hasta": str(bt_cfg.fecha_hasta),
                    "capital_inicial": bt_cfg.capital_inicial,
                    "cost_model": asdict(bt_cfg.cost_model),
                    "risk_limits": asdict(bt_cfg.risk_limits),
                    "benchmark_simbolo": bt_cfg.benchmark_simbolo,
                },
            },
            f,
            indent=2,
            default=str,
        )

    print(f"\n=== Resumen backtest — {strategy.__class__.__name__} vs. buy & hold {bt_cfg.benchmark_simbolo} ===")
    print(f"Período: {bt_cfg.fecha_desde} a {bt_cfg.fecha_hasta} | Capital inicial: ${bt_cfg.capital_inicial:,.2f}")
    print(f"Símbolos: {', '.join(price_data.keys())}")
    print(f"\n{'Métrica':<32} {'Estrategia':>15} {'Benchmark':>15}")
    print("-" * 64)
    for label, val_e, val_b in _filas_comparacion(metrics_estrategia, metrics_benchmark):
        print(f"{label:<32} {val_e:>15} {val_b:>15}")
    print(f"\nDetalle guardado en {out_dir}")


if __name__ == "__main__":
    main()
