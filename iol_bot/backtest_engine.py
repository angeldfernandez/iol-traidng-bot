"""Motor de backtesting: corre una Strategy (iol_bot/strategy.py) día por día sobre históricos
reales, reusando SIN modificar la misma lógica de riesgo que corre en vivo/paper trading
(apply_position_override, RiskManager) — el objetivo es validar el comportamiento real del bot, no
inventar una simulación aparte.

Nota de diseño importante: para cada símbolo/día se le pasa a strategy.evaluate() un slice
CRECIENTE explícito del DataFrame (hasta esa fecha inclusive), no el DataFrame completo. No alcanza
con confiar en que sma/rsi/etc. son "causales" (rolling/ewm no miran para adelante) — Strategy.
evaluate() siempre lee la ÚLTIMA fila del DataFrame que se le pasa como "hoy", así que si se le
pasara el historial completo, todos los días simulados leerían la fecha real más reciente en vez de
la fecha que se está simulando. El slice explícito es la única forma correcta de evitar esto."""
import logging
from dataclasses import dataclass, field

import pandas as pd

from iol_bot.backtest_portfolio import BacktestPortfolio
from iol_bot.risk import RiskManager, apply_position_override
from iol_bot.strategy import Signal

logger = logging.getLogger("iol_bot.backtest_engine")


@dataclass
class BacktestResult:
    equity_curve: pd.Series  # index=fecha, valor=cartera total valorizada ese día
    trades: pd.DataFrame  # una fila por ejecución (compra o venta), ver BacktestPortfolio.trade_log
    exposure_curve: pd.Series  # index=fecha, valor = (valorizado - cash) / valorizado ese día
    halted_days: list = field(default_factory=list)  # fechas en que el circuit breaker bloqueó compras


def run_backtest(strategy, price_data, fecha_desde, fecha_hasta, capital_inicial, cost_model, risk_limits, orden_simbolos=None):
    """price_data: dict simbolo -> price_df (columnas fecha/apertura/maximo/minimo/cierre/variacion).

    orden_simbolos: orden de procesamiento dentro de cada día (default: alfabético). Importa de
    verdad, no solo para reproducibilidad: con cash limitado y varias señales BUY el mismo día, el
    símbolo procesado primero es el que se queda con el margen disponible.
    """
    desde_ts, hasta_ts = pd.Timestamp(fecha_desde), pd.Timestamp(fecha_hasta)
    simbolos = orden_simbolos or sorted(price_data)

    dfs = {}
    indices = {}
    for simbolo in simbolos:
        df = price_data[simbolo]
        df = df[(df["fecha"] >= desde_ts) & (df["fecha"] <= hasta_ts)].sort_values("fecha").reset_index(drop=True)
        dfs[simbolo] = df
        indices[simbolo] = {fecha: i for i, fecha in enumerate(df["fecha"])}

    todas_las_fechas = sorted(set().union(*(set(df["fecha"]) for df in dfs.values())))
    if not todas_las_fechas:
        logger.warning("run_backtest: no hay ninguna fecha con datos en el rango [%s, %s]", fecha_desde, fecha_hasta)
        vacio = pd.Series(dtype=float)
        return BacktestResult(vacio, pd.DataFrame(), vacio, [])

    portfolio = BacktestPortfolio(capital_inicial, cost_model)
    risk_manager = RiskManager(risk_limits, state_path=None)  # 100% en memoria, sin tocar disco

    equity_puntos = []
    exposure_puntos = []
    halted_days = []
    dia_actual = None
    # RiskManager fue diseñado para MUCHOS updates por día (ciclos en vivo cada 15 min), donde el
    # primer update del día ancla el baseline "temprano" y los siguientes detectan el movimiento
    # intradía. Acá solo hay UNA barra por símbolo por día: si se llamara update_portfolio_value
    # una sola vez (con el valor de hoy), el baseline se fijaría en ese mismo valor y el PnL
    # diario daría siempre 0 — el circuit breaker nunca se activaría. Por eso se ancla el baseline
    # al CIERRE DEL DÍA ANTERIOR antes de marcar los precios de hoy, y recién después se
    # actualiza con el valor de hoy — así un gap/salto de un día para el otro sí se detecta antes
    # de dejar operar ese día.
    valor_cierre_dia_anterior = capital_inicial

    for fecha in todas_las_fechas:
        if dia_actual is None or fecha.date() != dia_actual:
            risk_manager.reset_daily()
            risk_manager.update_portfolio_value(valor_cierre_dia_anterior)
            dia_actual = fecha.date()

        precios_hoy = {}
        for simbolo in simbolos:
            idx = indices[simbolo].get(fecha)
            if idx is not None:
                precios_hoy[simbolo] = float(dfs[simbolo]["cierre"].iloc[idx])

        portfolio_value = portfolio.valorizado_total(precios_hoy)
        risk_manager.update_portfolio_value(portfolio_value)
        if risk_manager.is_halted():
            halted_days.append(fecha)

        for simbolo in simbolos:
            idx = indices[simbolo].get(fecha)
            if idx is None:
                continue  # símbolo sin barra este día (hueco/IPO tardío/delisting) — se saltea

            slice_hasta_hoy = dfs[simbolo].iloc[: idx + 1]
            precio_actual = precios_hoy[simbolo]

            trade_signal = strategy.evaluate(simbolo, slice_hasta_hoy)
            posicion = portfolio.positions.get(simbolo, {"cantidad": 0, "costo_promedio": 0.0})
            trade_signal = apply_position_override(
                trade_signal, posicion["cantidad"], posicion.get("costo_promedio"), risk_limits
            )

            if trade_signal.signal == Signal.BUY:
                cantidad, _motivo = risk_manager.size_buy_order(
                    precio_actual, portfolio.valorizado_total(precios_hoy), portfolio.exposicion(simbolo, precio_actual)
                )
                if cantidad > 0:
                    portfolio.buy(fecha, simbolo, cantidad, precio_actual)

            elif trade_signal.signal == Signal.SELL:
                cantidad_disponible = posicion["cantidad"]
                if cantidad_disponible > 0:
                    aprobado, _motivo = risk_manager.approve_sell()
                    if aprobado:
                        portfolio.sell(fecha, simbolo, cantidad_disponible, precio_actual)

        valor_final_dia = portfolio.valorizado_total(precios_hoy)
        valor_posiciones = valor_final_dia - portfolio.cash
        equity_puntos.append((fecha, valor_final_dia))
        exposure_puntos.append((fecha, valor_posiciones / valor_final_dia if valor_final_dia else 0.0))
        valor_cierre_dia_anterior = valor_final_dia

    equity_curve = pd.Series(dict(equity_puntos)).sort_index()
    exposure_curve = pd.Series(dict(exposure_puntos)).sort_index()
    trades = pd.DataFrame(portfolio.trade_log)

    return BacktestResult(equity_curve, trades, exposure_curve, halted_days)
