"""Cartera virtual con costos de transacción, para backtesting. Separada de PaperPortfolio
(iol_bot/paper_portfolio.py) a propósito: PaperPortfolio persiste a disco en cada compra/venta (bien
para paper trading a ritmo humano, no para un loop de backtest con potencialmente miles de
operaciones) y no modela costos — acá sí, porque un backtest sin costos es sistemáticamente
optimista (sección 22 del prompt maestro: "nunca mostrar resultados de backtest sin costos")."""
from dataclasses import dataclass


@dataclass
class CostModel:
    """Valores PLACEHOLDER, no son tarifas reales verificadas de IOL. Antes de confiar en un
    backtest para decidir algo, contrastá estos porcentajes contra tu resumen de cuenta real o la
    tabla de aranceles vigente de IOL (invertironline.com/tarifas) y ajustalos en config/backtest.yaml."""

    comision_pct: float = 0.5
    derechos_mercado_pct: float = 0.08
    slippage_pct: float = 0.05

    def total_cost_pct(self):
        return self.comision_pct + self.derechos_mercado_pct + self.slippage_pct


class BacktestPortfolio:
    """cash + posiciones (costo promedio ponderado, misma convención que PaperPortfolio) con
    costos de transacción aplicados en cada compra/venta. trade_log queda en memoria — quien corre
    el backtest lo persiste una sola vez al final (ver scripts/run_backtest.py)."""

    def __init__(self, initial_cash, cost_model=None):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions = {}  # simbolo -> {"cantidad": int, "costo_promedio": float}
        self.cost_model = cost_model or CostModel()
        self.trade_log = []

    def buy(self, fecha, simbolo, cantidad, precio_mercado):
        if cantidad <= 0:
            return False

        # Slippage adverso: al comprar, en la práctica pagás un poco más del precio de referencia.
        precio_ejecucion = precio_mercado * (1 + self.cost_model.slippage_pct / 100)
        monto_bruto = cantidad * precio_ejecucion
        comision = monto_bruto * self.cost_model.comision_pct / 100
        derechos = monto_bruto * self.cost_model.derechos_mercado_pct / 100
        costo_total = monto_bruto + comision + derechos

        if costo_total > self.cash:
            return False

        pos = self.positions.get(simbolo, {"cantidad": 0, "costo_promedio": 0.0})
        nueva_cantidad = pos["cantidad"] + cantidad
        nuevo_costo_promedio = (pos["cantidad"] * pos["costo_promedio"] + costo_total) / nueva_cantidad
        self.positions[simbolo] = {"cantidad": nueva_cantidad, "costo_promedio": nuevo_costo_promedio}
        self.cash -= costo_total

        self._log(fecha, simbolo, "COMPRA", cantidad, precio_mercado, precio_ejecucion, comision, derechos, None)
        return True

    def sell(self, fecha, simbolo, cantidad, precio_mercado):
        pos = self.positions.get(simbolo)
        if not pos or cantidad <= 0 or pos["cantidad"] < cantidad:
            return False, 0.0

        # Slippage adverso: al vender, en la práctica cobrás un poco menos del precio de referencia.
        precio_ejecucion = precio_mercado * (1 - self.cost_model.slippage_pct / 100)
        monto_bruto = cantidad * precio_ejecucion
        comision = monto_bruto * self.cost_model.comision_pct / 100
        derechos = monto_bruto * self.cost_model.derechos_mercado_pct / 100
        ingreso_neto = monto_bruto - comision - derechos
        pnl_realizado = ingreso_neto - cantidad * pos["costo_promedio"]

        restante = pos["cantidad"] - cantidad
        if restante == 0:
            del self.positions[simbolo]
        else:
            self.positions[simbolo] = {"cantidad": restante, "costo_promedio": pos["costo_promedio"]}
        self.cash += ingreso_neto

        self._log(fecha, simbolo, "VENTA", cantidad, precio_mercado, precio_ejecucion, comision, derechos, pnl_realizado)
        return True, pnl_realizado

    def exposicion(self, simbolo, precio_actual):
        pos = self.positions.get(simbolo)
        if not pos:
            return 0.0
        return pos["cantidad"] * precio_actual

    def valorizado_total(self, precios_actuales):
        valor_posiciones = sum(
            pos["cantidad"] * precios_actuales.get(simbolo, pos["costo_promedio"])
            for simbolo, pos in self.positions.items()
        )
        return self.cash + valor_posiciones

    def _log(self, fecha, simbolo, lado, cantidad, precio_mercado, precio_ejecucion, comision, derechos, pnl_realizado):
        self.trade_log.append(
            {
                "fecha": fecha,
                "simbolo": simbolo,
                "lado": lado,
                "cantidad": cantidad,
                "precio_mercado": precio_mercado,
                "precio_ejecucion": precio_ejecucion,
                "comision": comision,
                "derechos": derechos,
                "pnl_realizado": pnl_realizado,
            }
        )
