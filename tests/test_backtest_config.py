import pytest

from iol_bot.backtest_config import BacktestConfig
from iol_bot.config import RiskLimits

BASE_YAML = """
universe:
  simbolos:
    - {{simbolo: GGAL, mercado: bCBA}}
date_range:
  desde: "{desde}"
  hasta: "{hasta}"
capital_inicial: 100000.0
costs:
  comision_pct: 0.5
  derechos_mercado_pct: 0.1
  slippage_pct: 0.05
metrics:
  risk_free_rate_annual: 0.0
risk_override: {risk_override}
benchmark:
  simbolo: {benchmark_simbolo}
  mercado: {benchmark_mercado}
"""


def _write_yaml(tmp_path, **overrides):
    valores = dict(desde="2023-01-01", hasta="2024-01-01", risk_override="null", benchmark_simbolo="null", benchmark_mercado="null")
    valores.update(overrides)
    path = tmp_path / "backtest.yaml"
    path.write_text(BASE_YAML.format(**valores), encoding="utf-8")
    return path


def _default_risk_limits():
    return RiskLimits(
        max_monto_por_orden_pct=20, max_exposicion_por_simbolo_pct=20, max_perdida_diaria_pct=5, take_profit_pct=8, stop_loss_pct=5
    )


EMPTY_UNIVERSE_YAML = """
universe:
  simbolos: []
date_range:
  desde: "2023-01-01"
  hasta: "2024-01-01"
capital_inicial: 100000.0
risk_override: null
benchmark:
  simbolo: SPY
  mercado: bCBA
"""


def test_empty_universe_raises(tmp_path):
    path = tmp_path / "backtest.yaml"
    path.write_text(EMPTY_UNIVERSE_YAML, encoding="utf-8")

    with pytest.raises(ValueError, match="no puede estar vacío"):
        BacktestConfig.load(path, default_risk_limits=_default_risk_limits(), default_benchmark=("SPY", "bCBA"))


def test_desde_after_hasta_raises(tmp_path):
    path = _write_yaml(tmp_path, desde="2024-06-01", hasta="2024-01-01", benchmark_simbolo="SPY", benchmark_mercado="bCBA")

    with pytest.raises(ValueError, match="debe ser anterior"):
        BacktestConfig.load(path, default_risk_limits=_default_risk_limits(), default_benchmark=("SPY", "bCBA"))


def test_risk_override_absent_falls_back_to_default(tmp_path):
    path = _write_yaml(tmp_path, benchmark_simbolo="SPY", benchmark_mercado="bCBA")
    default_limits = _default_risk_limits()

    cfg = BacktestConfig.load(path, default_risk_limits=default_limits, default_benchmark=("SPY", "bCBA"))

    assert cfg.risk_limits is default_limits


def test_risk_override_present_overrides_default(tmp_path):
    override = "{max_monto_por_orden_pct: 50, max_exposicion_por_simbolo_pct: 50, max_perdida_diaria_pct: 10, take_profit_pct: 15, stop_loss_pct: 10}"
    path = _write_yaml(tmp_path, risk_override=override, benchmark_simbolo="SPY", benchmark_mercado="bCBA")

    cfg = BacktestConfig.load(path, default_risk_limits=_default_risk_limits(), default_benchmark=("SPY", "bCBA"))

    assert cfg.risk_limits.max_monto_por_orden_pct == 50
    assert cfg.risk_limits.max_perdida_diaria_pct == 10


def test_missing_risk_override_and_no_default_raises(tmp_path):
    path = _write_yaml(tmp_path, benchmark_simbolo="SPY", benchmark_mercado="bCBA")

    with pytest.raises(ValueError, match="risk_override"):
        BacktestConfig.load(path, default_risk_limits=None, default_benchmark=("SPY", "bCBA"))


def test_benchmark_null_falls_back_to_default(tmp_path):
    path = _write_yaml(tmp_path)  # benchmark queda null

    cfg = BacktestConfig.load(path, default_risk_limits=_default_risk_limits(), default_benchmark=("SPY", "bCBA"))

    assert cfg.benchmark_simbolo == "SPY"
    assert cfg.benchmark_mercado == "bCBA"


def test_benchmark_explicit_overrides_default(tmp_path):
    path = _write_yaml(tmp_path, benchmark_simbolo="QQQ", benchmark_mercado="bCBA")

    cfg = BacktestConfig.load(path, default_risk_limits=_default_risk_limits(), default_benchmark=("SPY", "bCBA"))

    assert cfg.benchmark_simbolo == "QQQ"


def test_loads_real_default_config_file():
    cfg = BacktestConfig.load(default_risk_limits=_default_risk_limits(), default_benchmark=("SPY", "bCBA"))

    assert len(cfg.simbolos) > 0
    assert cfg.fecha_desde < cfg.fecha_hasta
    assert cfg.capital_inicial > 0
