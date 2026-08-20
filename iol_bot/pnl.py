from datetime import date, datetime

# Slots categóricos 1-6 en el orden validado del skill de dataviz (pasan el chequeo de pares
# adyacentes para stacks/barras) — usados por composicion_cartera, que asigna un slot a cada
# símbolo por orden de tamaño (no por identidad fija: la cartera cambia de contenido día a día, así
# que lo estable es "la posición más grande siempre es el slot 1").
COMPOSICION_SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
COLOR_MUTED = "#898781"
COLOR_OTROS = "#4a3aa7"  # slot 7 (violet) — balde para símbolos que no entran en los 6 slots


def composicion_cartera(cash, positions, precios_cache, max_simbolos=len(COMPOSICION_SLOTS)):
    """Arma los segmentos del gráfico de composición de la cartera de paper trading: cash (color
    fijo, gris) + hasta `max_simbolos` posiciones más grandes (un slot categórico cada una, por
    orden de tamaño) + un balde "Otros" si hay más símbolos que slots disponibles — nunca se
    generan más de len(COMPOSICION_SLOTS) colores categóricos nuevos (ver skill de dataviz: una
    posición fuera de los slots validados no se le inventa un color, se agrupa)."""
    filas = [
        {
            "nombre": simbolo,
            "valor": p["cantidad"] * (precios_cache.get(simbolo) if precios_cache.get(simbolo) is not None else p["costo_promedio"]),
        }
        for simbolo, p in positions.items()
    ]
    filas.sort(key=lambda f: f["valor"], reverse=True)

    segmentos = [{"nombre": "Cash disponible", "valor": cash, "color": COLOR_MUTED}]
    for i, fila in enumerate(filas[:max_simbolos]):
        segmentos.append({"nombre": fila["nombre"], "valor": fila["valor"], "color": COMPOSICION_SLOTS[i]})

    resto = filas[max_simbolos:]
    if resto:
        segmentos.append(
            {"nombre": f"Otros ({len(resto)})", "valor": sum(f["valor"] for f in resto), "color": COLOR_OTROS}
        )

    segmentos.sort(key=lambda s: s["valor"], reverse=True)
    return segmentos


def ganancia_diaria_para_mostrar(paper_risk_status, hoy=None):
    """Interpreta logs/paper_risk_state.json (RiskManager, ver iol_bot/risk.py) para la métrica
    "Ganancia/pérdida HOY" del dashboard. Nunca calcula el % contra un baseline en falso -- hay
    tres estados donde NO hay ganancia real de hoy para mostrar, y hay que distinguirlos:

    - "otro_dia": el último dato guardado es de un día anterior (antes del primer ciclo de hoy, o
      el proceso se cayó ayer y no volvió a correr).
    - "esperando_primer_ciclo": RiskManager ya se autocorrigió hoy (reset_daily al arrancar el
      proceso) pero todavía no corrió ningún ciclo real que fije la línea base -- baseline_value
      queda en None y daily_pnl en 0.0 en ese momento, que NO es "hoy vamos empatados" sino
      "todavía no hay dato de hoy" (ej. el proceso arrancó pero TRADING_START_TIME, ver
      iol_bot/config.py, todavía no llegó).
    - "sin_datos": no hay ningún estado persistido todavía (nunca corrió paper trading).

    Devuelve {"estado": ..., "ganancia": float|None, "ganancia_pct": float|None}."""
    hoy = hoy or date.today()

    if not paper_risk_status:
        return {"estado": "sin_datos", "ganancia": None, "ganancia_pct": None}

    actualizado_en = paper_risk_status.get("updated_at")
    es_de_hoy = False
    if actualizado_en:
        try:
            es_de_hoy = datetime.fromisoformat(actualizado_en).date() == hoy
        except ValueError:
            es_de_hoy = False

    if not es_de_hoy:
        return {"estado": "otro_dia", "ganancia": None, "ganancia_pct": None}

    baseline_dia = paper_risk_status.get("baseline_value")
    if not baseline_dia:
        return {"estado": "esperando_primer_ciclo", "ganancia": None, "ganancia_pct": None}

    ganancia = paper_risk_status.get("daily_pnl")
    if ganancia is None:
        return {"estado": "esperando_primer_ciclo", "ganancia": None, "ganancia_pct": None}

    return {"estado": "ok", "ganancia": ganancia, "ganancia_pct": ganancia / baseline_dia * 100}


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
