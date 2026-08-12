import logging

from iol_bot.client import IOLApiError

logger = logging.getLogger("iol_bot.market_scanner")

DEFAULT_PANELES = ["merval", "burcap", "cedears"]
INSTRUMENTO = "acciones"  # único instrumento soportado hoy: acciones argentinas y CEDEARs
MERCADO = "bCBA"  # todo lo que devuelven estos paneles cotiza en bCBA/BYMA


def scan_market(client, paneles=None, top_n=50, pais="argentina"):
    """Trae cotizaciones de los paneles indicados (una llamada HTTP por panel — barato,
    a diferencia de pedir serie histórica símbolo por símbolo) y devuelve los `top_n` símbolos de
    mayor volumen operado en pesos (liquidez), para no gastar cientos de llamadas de serie
    histórica en instrumentos casi sin operar. Símbolos repetidos entre paneles se deduplican
    quedándose con el de mayor volumen visto."""
    paneles = paneles or DEFAULT_PANELES
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

    ranked = sorted(vistos.values(), key=lambda item: item["volumen_nominal"], reverse=True)
    seleccionados = ranked[:top_n]

    logger.info(
        "Scan de mercado: %d símbolos únicos en %d panel(es), analizando los %d de mayor volumen nominal",
        len(vistos),
        len(paneles),
        len(seleccionados),
    )
    if not seleccionados:
        logger.warning("El scan de mercado no devolvió ningún símbolo — revisar paneles configurados")

    return [
        {"simbolo": item["simbolo"], "mercado": MERCADO, "ultimo_precio": item["ultimo_precio"]}
        for item in seleccionados
    ]
