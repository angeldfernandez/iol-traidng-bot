import json

import requests
import responses

from iol_bot.auth import IOLAuth
from iol_bot.client import IOLApiError, IOLClient

BASE_URL = "https://api.invertironline.com"


def _authed_client():
    auth = IOLAuth(BASE_URL, "user", "pass")
    auth._access_token = "tok"
    auth._expires_at = 10**12  # no expira durante el test
    return IOLClient(BASE_URL, auth)


@responses.activate
def test_cotizacion_hits_expected_path():
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/bCBA/Titulos/GGAL/Cotizacion",
        json={"ultimoPrecio": 123.4},
        status=200,
    )
    client = _authed_client()
    data = client.cotizacion("GGAL", mercado="bCBA")
    assert data["ultimoPrecio"] == 123.4


@responses.activate
def test_serie_historica_hits_expected_path():
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/bCBA/Titulos/GGAL/Cotizacion/seriehistorica/2024-01-01/2024-02-01/ajustada",
        json=[{"fechaHora": "2024-01-02T00:00:00", "ultimoPrecio": 100}],
        status=200,
    )
    client = _authed_client()
    data = client.serie_historica("GGAL", "2024-01-01", "2024-02-01", ajustada="ajustada")
    assert len(data) == 1


@responses.activate
def test_portafolio_hits_expected_path_without_country_param():
    responses.add(responses.GET, f"{BASE_URL}/api/portafolio", json={"activos": []}, status=200)
    client = _authed_client()
    data = client.portafolio()
    assert data == {"activos": []}


@responses.activate
def test_estado_cuenta_hits_expected_path():
    responses.add(responses.GET, f"{BASE_URL}/api/estadocuenta", json={"totalEnPesos": 1000}, status=200)
    client = _authed_client()
    assert client.estado_cuenta()["totalEnPesos"] == 1000


@responses.activate
def test_comprar_sends_expected_body():
    responses.add(responses.POST, f"{BASE_URL}/api/operar/Comprar", json={"ok": True, "messages": []}, status=200)
    client = _authed_client()
    result = client.comprar("GGAL", cantidad=10, precio=100.5, validez="2024-02-01")
    assert result["ok"] is True

    sent_body = json.loads(responses.calls[0].request.body)
    assert sent_body["mercado"] == "bCBA"
    assert sent_body["simbolo"] == "GGAL"
    assert sent_body["cantidad"] == 10
    assert sent_body["precio"] == 100.5


@responses.activate
def test_vender_sends_expected_body():
    responses.add(responses.POST, f"{BASE_URL}/api/operar/Vender", json={"ok": True, "messages": []}, status=200)
    client = _authed_client()
    result = client.vender("GGAL", cantidad=5, precio=99.0, validez="2024-02-01")
    assert result["ok"] is True

    sent_body = json.loads(responses.calls[0].request.body)
    assert sent_body["simbolo"] == "GGAL"
    assert sent_body["cantidad"] == 5


@responses.activate
def test_401_triggers_refresh_and_retry():
    responses.add(responses.GET, f"{BASE_URL}/api/estadocuenta", status=401)
    responses.add(
        responses.POST,
        f"{BASE_URL}/token",
        json={"access_token": "tok2", "refresh_token": "ref2", "expires_in": 900},
        status=200,
    )
    responses.add(responses.GET, f"{BASE_URL}/api/estadocuenta", json={"totalEnPesos": 1}, status=200)

    client = _authed_client()
    client.auth._refresh_token = "ref1"
    data = client.estado_cuenta()
    assert data["totalEnPesos"] == 1


@responses.activate
def test_error_response_raises_iolapierror():
    responses.add(responses.GET, f"{BASE_URL}/api/estadocuenta", json={"error": "boom"}, status=500)
    client = _authed_client()
    try:
        client.estado_cuenta()
        assert False, "debería haber lanzado IOLApiError"
    except IOLApiError:
        pass


@responses.activate
def test_network_error_is_wrapped_as_iolapierror():
    # DNS caído, timeout, conexión rechazada, etc. — no es una respuesta HTTP con status >=400,
    # pero el resto del código solo sabe manejar IOLApiError. Ver iol_bot/client.py::_request.
    responses.add(
        responses.GET,
        f"{BASE_URL}/api/estadocuenta",
        body=requests.exceptions.ConnectionError("getaddrinfo failed"),
    )
    client = _authed_client()
    try:
        client.estado_cuenta()
        assert False, "debería haber lanzado IOLApiError"
    except IOLApiError as exc:
        assert "error de red" in str(exc)
