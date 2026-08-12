import json
import logging
import math
from datetime import datetime

logger = logging.getLogger("iol_bot.risk")


def calc_buy_quantity(precio, monto_max_orden, portfolio_value, exposicion_actual_simbolo, max_exposicion_pct):
    """Cantidad de unidades a comprar respetando el monto máx. por orden y el tope de exposición
    por símbolo. Devuelve 0 si no hay margen disponible."""
    if precio <= 0 or portfolio_value <= 0:
        return 0

    exposicion_max_valor = portfolio_value * (max_exposicion_pct / 100)
    margen_disponible = exposicion_max_valor - exposicion_actual_simbolo
    monto_a_usar = min(monto_max_orden, max(margen_disponible, 0))

    return math.floor(monto_a_usar / precio)


def check_daily_circuit_breaker(daily_pnl, portfolio_value, max_perdida_diaria_pct):
    """True si la pérdida acumulada del día ya superó el umbral configurado."""
    if portfolio_value <= 0:
        return False
    perdida_pct = (-daily_pnl / portfolio_value) * 100
    return perdida_pct >= max_perdida_diaria_pct


def check_position_exit(precio_actual, costo_promedio, take_profit_pct, stop_loss_pct):
    """Toma de ganancia / stop-loss atados al precio de compra de la posición — independiente de
    lo que diga la estrategia (tendencia/RSI son señales de mercado, no saben cuánto ganamos o
    perdimos en la posición puntual). Devuelve (debe_vender, motivo)."""
    if costo_promedio is None or costo_promedio <= 0 or precio_actual is None:
        return False, None

    ganancia_pct = (precio_actual - costo_promedio) / costo_promedio * 100

    if ganancia_pct >= take_profit_pct:
        return True, f"take-profit: +{ganancia_pct:.1f}% (objetivo {take_profit_pct:.1f}%)"
    if ganancia_pct <= -stop_loss_pct:
        return True, f"stop-loss: {ganancia_pct:.1f}% (límite -{stop_loss_pct:.1f}%)"
    return False, None


def apply_position_override(trade_signal, cantidad_en_cartera, costo_promedio, limits):
    """Si ya hay posición en el símbolo, lo que pasó desde que se compró manda sobre la señal
    genérica de la estrategia:

    - Fuerza SELL si se llegó al take-profit o al stop-loss, sin importar qué diga la estrategia.
    - Si la estrategia sigue diciendo BUY, lo baja a HOLD — evita promediar hacia arriba sin límite
      mientras dure la tendencia (la estrategia por sí sola no sabe si ya estamos invertidos).

    Sin posición (cantidad_en_cartera == 0), la señal de la estrategia se usa tal cual."""
    from iol_bot.strategy import Signal, TradeSignal

    if cantidad_en_cartera <= 0:
        return trade_signal

    debe_salir, motivo_salida = check_position_exit(
        trade_signal.precio, costo_promedio, limits.take_profit_pct, limits.stop_loss_pct
    )
    if debe_salir:
        return TradeSignal(trade_signal.simbolo, Signal.SELL, trade_signal.precio, motivo_salida)

    if trade_signal.signal == Signal.BUY:
        return TradeSignal(
            trade_signal.simbolo, Signal.HOLD, trade_signal.precio, "ya en posición, no se promedia hacia arriba"
        )

    return trade_signal


class RiskManager:
    """Guarda el estado de riesgo entre ciclos del loop: compara el valor actual de la cartera
    (`estado_cuenta().totalEnPesos`) contra el valor al comienzo del día para saber si ya se
    superó el límite de pérdida diaria. Si se pasa `state_path`, persiste el estado a un JSON
    después de cada actualización, para que un proceso aparte (ej. el dashboard) lo pueda leer."""

    def __init__(self, limits, state_path=None):
        self.limits = limits
        self.state_path = state_path
        self._baseline_value = None
        self._daily_pnl = 0.0
        self._halted = False
        if state_path:
            self._load_state()

    def reset_daily(self):
        self._baseline_value = None
        self._daily_pnl = 0.0
        self._halted = False
        logger.info("Estado de riesgo diario reiniciado")
        self._save_state()

    def update_portfolio_value(self, portfolio_value):
        """Llamar una vez por ciclo con el valor actual de la cartera. La primera vez después de
        un reset_daily() fija ese valor como línea base del día."""
        if self._baseline_value is None:
            self._baseline_value = portfolio_value

        self._daily_pnl = portfolio_value - self._baseline_value

        if check_daily_circuit_breaker(self._daily_pnl, portfolio_value, self.limits.max_perdida_diaria_pct):
            if not self._halted:
                logger.warning(
                    "Circuit breaker activado: pérdida diaria %.2f (%.1f%% de la cartera) supera el límite del %.1f%%",
                    self._daily_pnl,
                    -self._daily_pnl / portfolio_value * 100 if portfolio_value else 0,
                    self.limits.max_perdida_diaria_pct,
                )
            self._halted = True

        self._save_state()

    def is_halted(self):
        return self._halted

    def size_buy_order(self, precio, portfolio_value, exposicion_actual_simbolo):
        if self._halted:
            return 0, "trading detenido por circuit breaker de pérdida diaria"

        cantidad = calc_buy_quantity(
            precio,
            self.limits.max_monto_por_orden,
            portfolio_value,
            exposicion_actual_simbolo,
            self.limits.max_exposicion_por_simbolo_pct,
        )
        if cantidad <= 0:
            return 0, "sin margen disponible (límite de monto o exposición por símbolo)"
        return cantidad, "ok"

    def approve_sell(self):
        if self._halted:
            return False, "trading detenido por circuit breaker de pérdida diaria"
        return True, "ok"

    def status(self):
        return {
            "baseline_value": self._baseline_value,
            "daily_pnl": self._daily_pnl,
            "halted": self._halted,
            "max_perdida_diaria_pct": self.limits.max_perdida_diaria_pct,
        }

    def _save_state(self):
        if not self.state_path:
            return
        data = self.status()
        data["updated_at"] = datetime.now().isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp_path.replace(self.state_path)

    def _load_state(self):
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("No se pudo leer el estado de riesgo persistido en %s, se arranca de cero", self.state_path)
            return
        self._baseline_value = data.get("baseline_value")
        self._daily_pnl = data.get("daily_pnl", 0.0)
        self._halted = data.get("halted", False)


def load_status(state_path):
    """Lectura standalone del estado de riesgo persistido, sin instanciar un RiskManager
    (usado por el dashboard, que no corre el loop del bot)."""
    if not state_path.exists():
        return None
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
