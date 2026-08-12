import logging

from iol_bot.client import IOLApiError

logger = logging.getLogger("iol_bot.market_scanner")

DEFAULT_PANELES = ["merval", "burcap", "cedears"]
INSTRUMENTO = "acciones"  # único instrumento soportado hoy: acciones argentinas y CEDEARs
MERCADO = "bCBA"  # todo lo que devuelven estos paneles cotiza en bCBA/BYMA


def _collect_ranked(client, paneles, pais):
    """Trae cotizaciones de los paneles indicados (una llamada HTTP por panel — barato,
    a diferencia de pedir serie histórica símbolo por símbolo) y devuelve TODOS los símbolos
    vistos, ordenados de mayor a menor volumen operado en pesos. Símbolos repetidos entre paneles
    se deduplican quedándose con el de mayor volumen visto."""
    vistos = {}

    for panel in paneles:
        try:
            data = client.panel_cotizaciones(INSTRUMENTO, panel, pais)
        except IOLApiError as exc:
            logger.error("No se pudo traer el panel '%s': %s", panel, exc)
            continue

        titulos = data.get("titulos", [])
        for titulo in titulos:
            simbolo = titulo.get("simbolo")
            if not simbolo:
                continue
            precio = titulo.get("ultimoPrecio") or 0
            volumen_nominal = (titulo.get("volumen") or 0) * precio

            existente = vistos.get(simbolo)
            if existente is None or volumen_nominal > existente["volumen_nominal"]:
                vistos[simbolo] = {"simbolo": simbolo, "volumen_nominal": volumen_nominal, "ultimo_precio": precio}

        logger.info("Panel '%s': %d símbolos", panel, len(titulos))

    return sorted(vistos.values(), key=lambda item: item["volumen_nominal"], reverse=True)


def scan_market(client, paneles=None, top_n=50, pais="argentina"):
    """Devuelve los `top_n` símbolos de mayor volumen operado en pesos (liquidez), para no gastar
    cientos de llamadas de serie histórica en instrumentos casi sin operar. Usado por la
    estrategia en vivo/paper trading, que solo necesitan simbolo/mercado/ultimo_precio."""
    paneles = paneles or DEFAULT_PANELES
    ranked = _collect_ranked(client, paneles, pais)
    seleccionados = ranked[:top_n]

    logger.info(
        "Scan de mercado: %d símbolos únicos en %d panel(es), analizando los %d de mayor volumen nominal",
        len(ranked),
        len(paneles),
        len(seleccionados),
    )
    if not seleccionados:
        logger.warning("El scan de mercado no devolvió ningún símbolo — revisar paneles configurados")

    return [
        {"simbolo": item["simbolo"], "mercado": MERCADO, "ultimo_precio": item["ultimo_precio"]}
        for item in seleccionados
    ]


def scan_market_candidates(client, paneles=None, top_n=150, pais="argentina"):
    """Igual que scan_market, pero conserva volumen_nominal — lo usa el motor de ranking/scoring
    (iol_bot/ranking.py) para calcular el feature de liquidez del día. scan_market en sí no
    cambia de forma para no romper a sus consumidores actuales (main.py, paper_trade.py,
    dashboard.py)."""
    paneles = paneles or DEFAULT_PANELES
    ranked = _collect_ranked(client, paneles, pais)
    seleccionados = ranked[:top_n]

    logger.info(
        "Scan de candidatos: %d símbolos únicos en %d panel(es), pool de %d para el motor de scoring",
        len(ranked),
        len(paneles),
        len(seleccionados),
    )

    return [
        {
            "simbolo": item["simbolo"],
            "mercado": MERCADO,
            "ultimo_precio": item["ultimo_precio"],
            "volumen_nominal": item["volumen_nominal"],
        }
        for item in seleccionados
    ]
