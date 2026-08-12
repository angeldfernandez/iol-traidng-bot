"""Carga y valida config/backtest.yaml. Mismo patrón que iol_bot/scoring_config.py: dataclass +
classmethod `load`, config de investigación cuantitativa separada de la operativa (.env)."""
from dataclasses import dataclass, field
from datetime import date

import yaml

from iol_bot.backtest_portfolio import CostModel
from iol_bot.config import PROJECT_ROOT, RiskLimits

DEFAULT_BACKTEST_CONFIG_PATH = PROJECT_ROOT / "config" / "backtest.yaml"


@dataclass
class BacktestConfig:
    simbolos: list  # [{"simbolo": .., "mercado": ..}, ...]
    fecha_desde: date
    fecha_hasta: date
    capital_inicial: float
    cost_model: CostModel
    risk_free_rate_annual: float
    risk_limits: RiskLimits
    benchmark_simbolo: str
    benchmark_mercado: str

    @classmethod
    def load(cls, path=None, default_risk_limits=None, default_benchmark=None):
        """default_risk_limits: RiskLimits real de .env, usado si risk_override es null.
        default_benchmark: (simbolo, mercado) real de config/scoring.yaml, usado si benchmark
        queda en null. Ambos son obligatorios en la práctica (scripts/run_backtest.py los pasa),
        pero se aceptan None acá para que los tests puedan no depender de ScoringConfig/Config."""
        path = path or DEFAULT_BACKTEST_CONFIG_PATH
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        simbolos = (raw.get("universe") or {}).get("simbolos") or []
        if not simbolos:
            raise ValueError("backtest.yaml: universe.simbolos no puede estar vacío")

        rango = raw.get("date_range") or {}
        try:
            fecha_desde = date.fromisoformat(rango["desde"])
            fecha_hasta = date.fromisoformat(rango["hasta"])
        except KeyError as exc:
            raise ValueError(f"backtest.yaml: falta date_range.{exc.args[0]}") from exc

        if fecha_desde >= fecha_hasta:
            raise ValueError(f"backtest.yaml: date_range.desde ({fecha_desde}) debe ser anterior a hasta ({fecha_hasta})")

        risk_raw = raw.get("risk_override")
        if risk_raw:
            risk_limits = RiskLimits(**risk_raw)
        elif default_risk_limits is not None:
            risk_limits = default_risk_limits
        else:
            raise ValueError(
                "backtest.yaml: risk_override es null y no se pasó default_risk_limits — hace "
                "falta uno de los dos para saber con qué límites de riesgo correr el backtest"
            )

        bench = raw.get("benchmark") or {}
        benchmark_simbolo = bench.get("simbolo")
        benchmark_mercado = bench.get("mercado")
        if not benchmark_simbolo or not benchmark_mercado:
            if not default_benchmark:
                raise ValueError(
                    "backtest.yaml: benchmark.simbolo/mercado son null y no se pasó default_benchmark"
                )
            benchmark_simbolo = benchmark_simbolo or default_benchmark[0]
            benchmark_mercado = benchmark_mercado or default_benchmark[1]

        return cls(
            simbolos=simbolos,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
            capital_inicial=float(raw["capital_inicial"]),
            cost_model=CostModel(**(raw.get("costs") or {})),
            risk_free_rate_annual=float((raw.get("metrics") or {}).get("risk_free_rate_annual", 0.0)),
            risk_limits=risk_limits,
            benchmark_simbolo=benchmark_simbolo,
            benchmark_mercado=benchmark_mercado,
        )
