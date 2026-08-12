"""Prueba rápida y de solo lectura: login + datos de cuenta/mercado + señales de una muestra
del scan de mercado.

No llama a Comprar/Vender en ningún momento, así que es segura sin importar DRY_RUN/CONFIRM_LIVE_TRADING.
No depende del horario de mercado ni del loop de main.py — corre un solo ciclo y termina.

Uso: python -m scripts.smoke_test
"""
import logging

from iol_bot.auth import IOLAuth
from iol_bot.client import IOLApiError, IOLClient
from iol_bot.config import Config
from iol_bot.logging_config import setup_logging
from iol_bot.main import build_posiciones_por_simbolo
from iol_bot.market_data import get_historical_prices_cached
from iol_bot.market_scanner import scan_market
from iol_bot.strategy import SmaCrossoverRsiStrategy

logger = logging.getLogger("iol_bot.smoke_test")

# Muestra chica para que la prueba sea rápida — el bot real usa MAX_SIMBOLOS_A_ANALIZAR del .env.
SMOKE_TEST_TOP_N = 10


def main():
    setup_logging()
    config = Config.load()

    print("=== 1) Login ===")
    auth = IOLAuth(config.base_url, config.username, config.password)
    client = IOLClient(config.base_url, auth)
    auth.get_token()
    print("OK: token obtenido")

    print("\n=== 2) Estado de cuenta y portafolio ===")
    estado_cuenta = client.estado_cuenta()
    portafolio = client.portafolio()
    portfolio_value = float(estado_cuenta.get("totalEnPesos", 0.0))
    posiciones = build_posiciones_por_simbolo(portafolio)
    print(f"totalEnPesos: {portfolio_value}")
    print(f"posiciones actuales: {list(posiciones.keys()) or '(ninguna)'}")

    print(f"\n=== 3) Scan de mercado (paneles: {config.paneles}) ===")
    watchlist = scan_market(client, paneles=config.paneles, top_n=SMOKE_TEST_TOP_N)
    print(f"Muestra de {len(watchlist)} símbolos de mayor volumen: {[w['simbolo'] for w in watchlist]}")

    print("\n=== 4) Serie histórica (cacheada) + señal por símbolo de la muestra ===")
    strategy = SmaCrossoverRsiStrategy()
    for item in watchlist:
        simbolo, mercado = item["simbolo"], item["mercado"]
        try:
            price_df = get_historical_prices_cached(client, simbolo, mercado=mercado, hoy_precio=item.get("ultimo_precio"))
            signal = strategy.evaluate(simbolo, price_df)
            posicion = posiciones.get(simbolo, {"cantidad": 0, "valorizado": 0.0})
            print(
                f"{simbolo}: último={item.get('ultimo_precio')} "
                f"filas_historico={len(price_df)} señal={signal.signal.value} "
                f"({signal.motivo}) posición_actual={posicion['cantidad']}"
            )
        except IOLApiError as exc:
            print(f"{simbolo}: ERROR de API — {exc}")

    print("\nSmoke test terminado. No se envió ninguna orden (este script no llama a Comprar/Vender).")


if __name__ == "__main__":
    main()
