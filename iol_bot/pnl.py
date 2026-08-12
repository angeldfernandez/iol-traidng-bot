def daily_pnl_pesos(valorizado, variacion_diaria_pct):
    """Ganancia/pérdida en pesos de HOY para una posición, a partir de su valor actual
    (`valorizado`) y la variación % de precio de hoy (`variacionDiaria` de PosicionModel).

    Se resuelve exacto (no aproximado): si el precio subió variacion_diaria_pct% hoy, el valor de
    ayer era valorizado / (1 + variacion_diaria_pct/100), y la diferencia es la ganancia de hoy.
    """
    if valorizado is None or variacion_diaria_pct is None:
        return 0.0
    denominador = 100 + variacion_diaria_pct
    if denominador == 0:
        return 0.0
    return valorizado * variacion_diaria_pct / denominador
