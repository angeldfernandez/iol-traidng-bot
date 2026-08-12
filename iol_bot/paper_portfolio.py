import json
import logging
from datetime import datetime

logger = logging.getLogger("iol_bot.paper_portfolio")


class PaperPortfolio:
    """Cartera 100% virtual para paper trading: no toca la cuenta real ni la API de
    Comprar/Vender. Arranca con `initial_cash` en efectivo y ninguna posición, y las compras/ventas
    que decida el bot se resuelven acá adentro contra precios reales de mercado (de solo lectura).
    Se persiste a JSON para sobrevivir reinicios del script y para que el dashboard la pueda leer."""

    def __init__(self, state_path, initial_cash=100_000.0):
        self.state_path = state_path
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions = {}  # simbolo -> {"cantidad": int, "costo_promedio": float}
        es_sesion_nueva = not self.state_path.exists()
        self._load()
        if es_sesion_nueva:
            # Persistir el estado inicial ya al arrancar (no recién en la primera compra/venta),
            # para que el dashboard pueda mostrar el saldo virtual desde el minuto uno.
            self._save()

    def buy(self, simbolo, cantidad, precio):
        costo = cantidad * precio
        if cantidad <= 0 or costo > self.cash:
            return False

        pos = self.positions.get(simbolo, {"cantidad": 0, "costo_promedio": 0.0})
        nueva_cantidad = pos["cantidad"] + cantidad
        nuevo_costo_promedio = (pos["cantidad"] * pos["costo_promedio"] + costo) / nueva_cantidad
        self.positions[simbolo] = {"cantidad": nueva_cantidad, "costo_promedio": nuevo_costo_promedio}
        self.cash -= costo
        self._save()
        return True

    def sell(self, simbolo, cantidad, precio):
        pos = self.positions.get(simbolo)
        if not pos or cantidad <= 0 or pos["cantidad"] < cantidad:
            return False, 0.0

        ingreso = cantidad * precio
        pnl_realizado = (precio - pos["costo_promedio"]) * cantidad

        restante = pos["cantidad"] - cantidad
        if restante == 0:
            del self.positions[simbolo]
        else:
            self.positions[simbolo] = {"cantidad": restante, "costo_promedio": pos["costo_promedio"]}

        self.cash += ingreso
        self._save()
        return True, pnl_realizado

    def exposicion(self, simbolo, precio_actual):
        pos = self.positions.get(simbolo)
        if not pos:
            return 0.0
        return pos["cantidad"] * precio_actual

    def valorizado_total(self, precios_actuales):
        """precios_actuales: dict simbolo -> último precio conocido. Si un símbolo en cartera no
        está en el dict, se valoriza a su costo promedio (mejor estimación disponible sin pedir
        cotización extra para cada posición)."""
        valor_posiciones = sum(
            pos["cantidad"] * precios_actuales.get(simbolo, pos["costo_promedio"])
            for simbolo, pos in self.positions.items()
        )
        return self.cash + valor_posiciones

    def status(self, precios_actuales=None):
        precios_actuales = precios_actuales or {}
        valorizado = self.valorizado_total(precios_actuales)
        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "positions": self.positions,
            "valorizado_total": valorizado,
            "pnl_total": valorizado - self.initial_cash,
            "pnl_total_pct": ((valorizado - self.initial_cash) / self.initial_cash * 100) if self.initial_cash else 0,
        }

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "positions": self.positions,
            "updated_at": datetime.now().isoformat(),
        }
        tmp_path = self.state_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        tmp_path.replace(self.state_path)

    def _load(self):
        if not self.state_path.exists():
            return
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("No se pudo leer %s, se arranca una cartera virtual nueva", self.state_path)
            return
        self.initial_cash = data.get("initial_cash", self.initial_cash)
        self.cash = data.get("cash", self.initial_cash)
        self.positions = data.get("positions", {})


def load_status(state_path):
    """Lectura standalone sin instanciar PaperPortfolio (para el dashboard). Ignora `initial_cash`
    porque no tenemos precios en vivo acá — devuelve el JSON crudo persistido, o None si no existe."""
    if not state_path.exists():
        return None
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
