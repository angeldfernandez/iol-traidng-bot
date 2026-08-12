import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"

# IOL no tiene un entorno de API de pruebas: ni un subdominio de sandbox (no resuelve por DNS, y no
# aparece en la cuenta), ni la "Cuenta de práctica" del Simulador es alcanzable por API (IOL confirma
# que esa cuenta "no está vinculada a tu usuario ni a los servicios de API de InvertirOnline.com").
# En criollo: la API SIEMPRE opera tu cuenta real con dinero real. No hay forma de probar
# Comprar/Vender contra plata falsa. DRY_RUN + CONFIRM_LIVE_TRADING son la única red de seguridad.
DEFAULT_BASE_URL = "https://api.invertironline.com"


def _env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name, default):
    value = os.getenv(name)
    return float(value) if value else default


def _env_int(name, default):
    value = os.getenv(name)
    return int(value) if value else default


@dataclass
class RiskLimits:
    max_monto_por_orden: float
    max_exposicion_por_simbolo_pct: float
    max_perdida_diaria_pct: float
    take_profit_pct: float
    stop_loss_pct: float


@dataclass
class Config:
    username: str
    password: str
    dry_run: bool
    confirm_live_trading: bool
    base_url: str
    paneles: list = field(default_factory=list)
    max_simbolos_a_analizar: int = 50
    loop_interval_minutes: int = 15
    paper_initial_cash: float = 100_000.0
    risk: RiskLimits = None

    @property
    def effective_dry_run(self):
        """DRY_RUN=false por sí solo NO alcanza para operar en real: hace falta además
        CONFIRM_LIVE_TRADING=true. Sin esto, cualquier error tipeando DRY_RUN sigue siendo inofensivo."""
        return self.dry_run or not self.confirm_live_trading

    @classmethod
    def load(cls, env_file=None):
        load_dotenv(dotenv_path=env_file or (PROJECT_ROOT / ".env"))

        username = os.getenv("IOL_USERNAME", "")
        password = os.getenv("IOL_PASSWORD", "")

        if not username or not password:
            raise ValueError(
                "Faltan credenciales: definí IOL_USERNAME e IOL_PASSWORD en tu archivo .env. "
                "Son las de tu cuenta real de IOL — la API no tiene un entorno de pruebas separado."
            )

        paneles_raw = os.getenv("PANELES", "merval,burcap,cedears")
        paneles = [p.strip().lower() for p in paneles_raw.split(",") if p.strip()]

        risk = RiskLimits(
            max_monto_por_orden=_env_float("MAX_MONTO_POR_ORDEN", 50_000),
            max_exposicion_por_simbolo_pct=_env_float("MAX_EXPOSICION_POR_SIMBOLO_PCT", 20),
            max_perdida_diaria_pct=_env_float("MAX_PERDIDA_DIARIA_PCT", 5),
            take_profit_pct=_env_float("TAKE_PROFIT_PCT", 8),
            stop_loss_pct=_env_float("STOP_LOSS_PCT", 5),
        )

        base_url = os.getenv("IOL_BASE_URL_OVERRIDE", "").strip() or DEFAULT_BASE_URL

        return cls(
            username=username,
            password=password,
            dry_run=_env_bool("DRY_RUN", True),
            confirm_live_trading=_env_bool("CONFIRM_LIVE_TRADING", False),
            base_url=base_url,
            paneles=paneles,
            max_simbolos_a_analizar=_env_int("MAX_SIMBOLOS_A_ANALIZAR", 50),
            loop_interval_minutes=_env_int("LOOP_INTERVAL_MINUTES", 15),
            paper_initial_cash=_env_float("PAPER_INITIAL_CASH", 100_000.0),
            risk=risk,
        )
