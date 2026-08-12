import responses

from iol_bot.auth import IOLAuth
from iol_bot.client import IOLClient
from iol_bot.market_scanner import scan_market

BASE_URL = "https://api.invertironline.com"


def _authed_client():
    auth = IOLAuth(BASE_URL, "user", "pass")
    auth._access_token = "tok"
    auth._expires_at = 10**12
    return IOLClient(BASE_URL, auth)


def _panel(*titulos):
    return {"titulos": list(titulos)}


def _titulo(simbolo, ultimo_precio, volumen):
    return {"simbolo": simbolo, "ultimoPrecio": ultimo_precio, "volumen": volumen, "mercado": "BCBA"}


@responses.activate
def test_scan_market_ranks_by_nominal_volume_and_caps_at_top_n():
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/Cotizaciones/acciones/merval/argentina",
        json=_panel(
            _titulo("GGAL", 1000, 100),  # nominal 100_000
            _titulo("YPFD", 500, 50),  # nominal 25_000
        ),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/Cotizaciones/acciones/cedears/argentina",
        json=_panel(_titulo("AAPL", 10000, 500)),  # nominal 5_000_000
        status=200,
    )

    resultado = scan_market(_authed_client(), paneles=["merval", "cedears"], top_n=2)

    assert resultado == [
        {"simbolo": "AAPL", "mercado": "bCBA", "ultimo_precio": 10000},
        {"simbolo": "GGAL", "mercado": "bCBA", "ultimo_precio": 1000},
    ]


@responses.activate
def test_scan_market_dedupes_symbol_across_panels_keeping_max_volume():
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/Cotizaciones/acciones/merval/argentina",
        json=_panel(_titulo("GGAL", 1000, 10)),  # nominal 10_000
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/Cotizaciones/acciones/burcap/argentina",
        json=_panel(_titulo("GGAL", 1000, 500)),  # nominal 500_000, mismo símbolo, más volumen
        status=200,
    )

    resultado = scan_market(_authed_client(), paneles=["merval", "burcap"], top_n=10)

    assert resultado == [{"simbolo": "GGAL", "mercado": "bCBA", "ultimo_precio": 1000}]


@responses.activate
def test_scan_market_skips_panel_that_errors_and_continues():
    responses.add(responses.GET, f"{BASE_URL}/api/Cotizaciones/acciones/merval/argentina", status=500)
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/Cotizaciones/acciones/burcap/argentina",
        json=_panel(_titulo("PAMP", 100, 10)),
        status=200,
    )

    resultado = scan_market(_authed_client(), paneles=["merval", "burcap"], top_n=10)

    assert resultado == [{"simbolo": "PAMP", "mercado": "bCBA", "ultimo_precio": 100}]


@responses.activate
def test_scan_market_returns_empty_list_when_no_panels_available():
    responses.add(responses.GET, f"{BASE_URL}/api/Cotizaciones/acciones/merval/argentina", status=500)

    resultado = scan_market(_authed_client(), paneles=["merval"], top_n=10)

    assert resultado == []
