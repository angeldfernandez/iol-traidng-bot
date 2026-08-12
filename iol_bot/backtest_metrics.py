"""Métricas de un backtest (equity curve + trade log -> números) y curva de referencia buy & hold
para comparar. Todas las funciones son NaN-safe: nunca lanzan excepción por datos insuficientes,
loguean y devuelven NaN donde corresponda (una corrida con pocos datos debe seguir imprimiendo un
reporte, aunque algunas métricas queden en blanco)."""
import logging
import math

import pandas as pd

from iol_bot.backtest_portfolio import BacktestPortfolio

logger = logging.getLogger("iol_bot.backtest_metrics")

TRADING_DAYS_YEAR = 252


def _years_elapsed(equity):
    if len(equity) < 2:
        return float("nan")
    return (equity.index[-1] - equity.index[0]).days / 365.25


def _cagr(equity):
    if len(equity) < 2 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return float("nan")
    n_anios = _years_elapsed(equity)
    if not n_anios or n_anios <= 0:
        return float("nan")
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / n_anios) - 1


def _max_drawdown_and_duration(equity):
    """Devuelve (max_drawdown [negativo o 0], duración en días desde el pico previo hasta la
    recuperación — o hasta el final de la serie si todavía no se recuperó)."""
    if equity.empty:
        return float("nan"), 0

    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_dd = float(drawdown.min())

    if max_dd == 0:
        return 0.0, 0

    idx_min = drawdown.idxmin()
    pico_valor = running_max.loc[idx_min]

    en_pico = equity.loc[:idx_min]
    fecha_pico = en_pico[en_pico >= pico_valor].index[-1]

    despues_del_piso = equity.loc[idx_min:]
    recuperada = despues_del_piso[despues_del_piso >= pico_valor]
    fecha_fin = recuperada.index[0] if not recuperada.empty else equity.index[-1]

    return max_dd, (fecha_fin - fecha_pico).days


def compute_metrics(equity, trades, exposure_curve, risk_free_rate_annual=0.0):
    """equity: pd.Series index=fecha, valor=cartera total valorizada. trades: DataFrame estilo
    BacktestPortfolio.trade_log (puede ser vacío/None — ej. un benchmark buy & hold sin ventas).
    exposure_curve: pd.Series index=fecha, valor en [0,1] (fracción de la cartera en posiciones)."""
    if equity is None or equity.empty:
        logger.warning("compute_metrics: equity curve vacía, no se pueden calcular métricas")
        return {}

    daily_returns = equity.pct_change().dropna()
    trades = trades if trades is not None else pd.DataFrame()

    retorno_total = float(equity.iloc[-1] / equity.iloc[0] - 1) if equity.iloc[0] else float("nan")
    cagr = _cagr(equity)
    retorno_anual_promedio = float(daily_returns.mean() * TRADING_DAYS_YEAR) if not daily_returns.empty else float("nan")
    volatilidad_anualizada = (
        float(daily_returns.std(ddof=1) * math.sqrt(TRADING_DAYS_YEAR)) if len(daily_returns) > 1 else float("nan")
    )

    rf_diaria = (1 + risk_free_rate_annual) ** (1 / TRADING_DAYS_YEAR) - 1
    exceso = daily_returns - rf_diaria

    desvio_exceso = exceso.std(ddof=1) if len(exceso) > 1 else float("nan")
    sharpe = float(exceso.mean() / desvio_exceso * math.sqrt(TRADING_DAYS_YEAR)) if desvio_exceso else float("nan")

    downside = exceso.clip(upper=0)
    downside_std = math.sqrt((downside**2).mean()) if len(downside) else float("nan")
    sortino = float(exceso.mean() / downside_std * math.sqrt(TRADING_DAYS_YEAR)) if downside_std else float("nan")

    max_dd, max_dd_dur = _max_drawdown_and_duration(equity)
    calmar = float(cagr / abs(max_dd)) if max_dd and not math.isnan(cagr) else float("nan")

    tiene_columnas_trade = not trades.empty and {"lado", "pnl_realizado"}.issubset(trades.columns)
    cerradas = trades[trades["lado"] == "VENTA"] if tiene_columnas_trade else pd.DataFrame()
    ganadoras = cerradas[cerradas["pnl_realizado"] > 0] if not cerradas.empty else cerradas
    perdedoras = cerradas[cerradas["pnl_realizado"] <= 0] if not cerradas.empty else cerradas

    numero_operaciones = len(cerradas)
    win_rate = float(len(ganadoras) / numero_operaciones) if numero_operaciones else float("nan")
    suma_ganadoras = float(ganadoras["pnl_realizado"].sum()) if not ganadoras.empty else 0.0
    suma_perdedoras = float(perdedoras["pnl_realizado"].sum()) if not perdedoras.empty else 0.0
    if suma_perdedoras:
        profit_factor = suma_ganadoras / abs(suma_perdedoras)
    else:
        profit_factor = float("inf") if suma_ganadoras > 0 else float("nan")
    avg_win = float(ganadoras["pnl_realizado"].mean()) if not ganadoras.empty else float("nan")
    avg_loss = float(perdedoras["pnl_realizado"].mean()) if not perdedoras.empty else float("nan")
    if numero_operaciones:
        expectancy = win_rate * (avg_win if not math.isnan(avg_win) else 0.0) + (1 - win_rate) * (
            avg_loss if not math.isnan(avg_loss) else 0.0
        )
    else:
        expectancy = float("nan")
    mejor_operacion = float(cerradas["pnl_realizado"].max()) if not cerradas.empty else float("nan")
    peor_operacion = float(cerradas["pnl_realizado"].min()) if not cerradas.empty else float("nan")

    tiene_columnas_costo = not trades.empty and {"cantidad", "precio_ejecucion"}.issubset(trades.columns)
    notional_operado = float((trades["cantidad"] * trades["precio_ejecucion"]).sum()) if tiene_columnas_costo else 0.0
    equity_promedio = float(equity.mean())
    n_anios = _years_elapsed(equity)
    turnover_anualizado = (
        notional_operado / equity_promedio / n_anios if equity_promedio and n_anios and n_anios > 0 else float("nan")
    )

    if not trades.empty and {"comision", "derechos", "precio_ejecucion", "precio_mercado", "cantidad"}.issubset(trades.columns):
        costos_comisiones_derechos = float((trades["comision"] + trades["derechos"]).sum())
        costo_slippage = float((trades["cantidad"] * (trades["precio_ejecucion"] - trades["precio_mercado"]).abs()).sum())
        costos_totales = costos_comisiones_derechos + costo_slippage
    else:
        costos_totales = 0.0

    exposicion_promedio = (
        float(exposure_curve.mean()) if exposure_curve is not None and not exposure_curve.empty else float("nan")
    )

    return {
        "retorno_total_pct": retorno_total * 100,
        "cagr_pct": cagr * 100 if not math.isnan(cagr) else float("nan"),
        "retorno_anual_promedio_pct": retorno_anual_promedio * 100,
        "volatilidad_anualizada_pct": volatilidad_anualizada * 100 if not math.isnan(volatilidad_anualizada) else float("nan"),
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": max_dd * 100 if not math.isnan(max_dd) else float("nan"),
        "max_drawdown_duracion_dias": max_dd_dur,
        "calmar": calmar,
        "numero_operaciones": numero_operaciones,
        "win_rate_pct": win_rate * 100 if not math.isnan(win_rate) else float("nan"),
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "mejor_operacion": mejor_operacion,
        "peor_operacion": peor_operacion,
        "turnover_anualizado": turnover_anualizado,
        "costos_totales": costos_totales,
        "exposicion_promedio_pct": exposicion_promedio * 100 if not math.isnan(exposicion_promedio) else float("nan"),
    }


def compute_buy_and_hold_equity(price_df, capital_inicial, cost_model, fecha_desde, fecha_hasta):
    """Compra el máximo de unidades posible al primer cierre disponible dentro de [desde, hasta]
    (aplicando el mismo cost_model que la estrategia, para que la comparación sea justa) y nunca
    vende — valoriza al cierre de cada día hasta fecha_hasta."""
    desde_ts, hasta_ts = pd.Timestamp(fecha_desde), pd.Timestamp(fecha_hasta)
    df = price_df[(price_df["fecha"] >= desde_ts) & (price_df["fecha"] <= hasta_ts)].sort_values("fecha").reset_index(drop=True)
    if df.empty:
        logger.warning("compute_buy_and_hold_equity: sin datos en el rango [%s, %s]", fecha_desde, fecha_hasta)
        return pd.Series(dtype=float)

    portfolio = BacktestPortfolio(capital_inicial, cost_model)
    precio_entrada = float(df["cierre"].iloc[0])

    # Cálculo exacto (no aproximado) del costo por unidad, replicando la fórmula de
    # BacktestPortfolio.buy(): costo_total = cantidad * precio_ejecucion * (1 + comision% + derechos%).
    precio_ejecucion = precio_entrada * (1 + cost_model.slippage_pct / 100)
    costo_por_unidad = precio_ejecucion * (1 + (cost_model.comision_pct + cost_model.derechos_mercado_pct) / 100)
    cantidad = math.floor(capital_inicial / costo_por_unidad) if costo_por_unidad > 0 else 0

    if cantidad > 0:
        portfolio.buy(df["fecha"].iloc[0], "BENCHMARK", cantidad, precio_entrada)

    puntos = {fila["fecha"]: portfolio.valorizado_total({"BENCHMARK": float(fila["cierre"])}) for _, fila in df.iterrows()}
    return pd.Series(puntos).sort_index()
