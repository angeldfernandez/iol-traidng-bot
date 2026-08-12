"""Carga y valida config/scoring.yaml (pesos y umbrales del motor de scoring/ranking).

Sigue el mismo patrón que iol_bot/config.py (dataclasses + un classmethod `load`), pero desde
YAML en vez de variables de entorno — acá vive configuración "de investigación cuantitativa"
(pesos, ventanas, filtros) separada de la configuración operativa del bot (.env)."""
from dataclasses import dataclass, field

import yaml

from iol_bot.config import PROJECT_ROOT

DEFAULT_SCORING_CONFIG_PATH = PROJECT_ROOT / "config" / "scoring.yaml"

_WEIGHT_SUM_TOLERANCE = 1e-6


@dataclass
class ScoringConfig:
    candidate_pool_size: int
    top_n_final: int
    min_history_days: int
    min_avg_volume: float
    min_price: float
    benchmark_simbolo: str
    benchmark_mercado: str
    groups: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    directionality_lower_is_better: set = field(default_factory=set)

    def is_lower_better(self, feature):
        return feature in self.directionality_lower_is_better

    @classmethod
    def load(cls, path=None):
        path = path or DEFAULT_SCORING_CONFIG_PATH
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        funnel = raw.get("funnel", {})
        filtros = raw.get("quality_filters", {})
        rs = raw.get("relative_strength", {})
        groups = raw.get("groups", {})
        weights = raw.get("weights", {})
        directionality = set(raw.get("directionality_lower_is_better", []))

        faltantes = set(weights) - set(groups)
        if faltantes:
            raise ValueError(
                f"scoring.yaml: weights referencia grupo(s) inexistentes en 'groups': {sorted(faltantes)}"
            )

        total_peso = sum(weights.values())
        if abs(total_peso - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"scoring.yaml: los pesos de 'weights' deben sumar 1.0, suman {total_peso}")

        return cls(
            candidate_pool_size=int(funnel.get("candidate_pool_size", 150)),
            top_n_final=int(funnel.get("top_n_final", 50)),
            min_history_days=int(filtros.get("min_history_days", 30)),
            min_avg_volume=float(filtros.get("min_avg_volume", 0)),
            min_price=float(filtros.get("min_price", 0)),
            benchmark_simbolo=rs.get("benchmark_simbolo", "SPY"),
            benchmark_mercado=rs.get("benchmark_mercado", "bCBA"),
            groups=groups,
            weights=weights,
            directionality_lower_is_better=directionality,
        )
