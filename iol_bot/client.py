import logging

import requests

logger = logging.getLogger("iol_bot.client")


class IOLApiError(Exception):
    pass


class IOLClient:
    """Wrapper delgado sobre la API REST de IOL (https://iol.apidocs.ar/)."""

    def __init__(self, base_url, auth, session=None):
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.session = session or requests.Session()

    def _request(self, method, path, retry_on_401=True, **kwargs):
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {})
        headers.update(self.auth.auth_header())

        try:
            response = self.session.request(method, url, headers=headers, timeout=20, **kwargs)
        except requests.exceptions.RequestException as exc:
            # DNS caído, timeout, conexión rechazada, etc. — no es un error HTTP, pero el resto
            # del código (main.py, paper_trade.py, ranking.py, market_scanner.py...) ya sabe
            # manejar IOLApiError en cada ciclo/símbolo. Envolverlo acá, en un solo lugar, evita
            # que un blip de red transitorio tumbe el proceso entero (pasó en producción: un
            # fallo de DNS de unos segundos mató la corrida de paper trading por 6 horas).
            raise IOLApiError(f"{method} {path} -> error de red: {exc}") from exc

        if response.status_code == 401 and retry_on_401:
            logger.info("401 recibido, forzando refresh de token y reintentando una vez")
            self.auth.force_refresh()
            return self._request(method, path, retry_on_401=False, **kwargs)

        if response.status_code >= 400:
            raise IOLApiError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}"
            )
        return response

    # --- Titulos ---

    def cotizacion(self, simbolo, mercado="bCBA", plazo=None):
        params = {"plazo": plazo} if plazo else {}
        resp = self._request("GET", f"/api/{mercado}/Titulos/{simbolo}/Cotizacion", params=params)
        return resp.json()

    def serie_historica(self, simbolo, fecha_desde, fecha_hasta, mercado="bCBA", ajustada="sinAjustar"):
        path = (
            f"/api/{mercado}/Titulos/{simbolo}/Cotizacion/seriehistorica/"
            f"{fecha_desde}/{fecha_hasta}/{ajustada}"
        )
        resp = self._request("GET", path)
        return resp.json()

    def panel_cotizaciones(self, instrumento, panel, pais="argentina"):
        """Cotizaciones de todos los símbolos de un panel en una sola llamada (ej. instrumento
        'acciones', panel 'merval'/'burcap'/'cedears'). Devuelve {"titulos": [...]}."""
        resp = self._request("GET", f"/api/Cotizaciones/{instrumento}/{panel}/{pais}")
        return resp.json()

    # --- Mi cuenta ---

    def portafolio(self):
        resp = self._request("GET", "/api/portafolio")
        return resp.json()

    def estado_cuenta(self):
        resp = self._request("GET", "/api/estadocuenta")
        return resp.json()

    def listar_operaciones(self, estado=None, fecha_desde=None, fecha_hasta=None):
        params = {}
        if estado:
            params["filtro.estado"] = estado
        if fecha_desde:
            params["filtro.fechaDesde"] = fecha_desde
        if fecha_hasta:
            params["filtro.fechaHasta"] = fecha_hasta
        resp = self._request("GET", "/api/operaciones", params=params)
        return resp.json()

    def cancelar_operacion(self, numero):
        resp = self._request("DELETE", f"/api/operaciones/{numero}")
        return resp.json()

    # --- Operar ---

    def comprar(self, simbolo, cantidad, precio, validez, mercado="bCBA", plazo="t0", tipo_orden="precioLimite"):
        body = {
            "mercado": mercado,
            "simbolo": simbolo,
            "cantidad": cantidad,
            "precio": precio,
            "plazo": plazo,
            "validez": validez,
            "tipoOrden": tipo_orden,
        }
        resp = self._request("POST", "/api/operar/Comprar", json=body)
        return resp.json()

    def vender(self, simbolo, cantidad, precio, validez, mercado="bCBA", plazo="t0", tipo_orden="precioLimite"):
        body = {
            "mercado": mercado,
            "simbolo": simbolo,
            "cantidad": cantidad,
            "precio": precio,
            "plazo": plazo,
            "validez": validez,
            "tipoOrden": tipo_orden,
        }
        resp = self._request("POST", "/api/operar/Vender", json=body)
        return resp.json()
