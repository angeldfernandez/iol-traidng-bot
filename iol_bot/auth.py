import logging
import time

import requests

logger = logging.getLogger("iol_bot.auth")

# Margen de seguridad para refrescar el token antes de que expire de verdad.
_EXPIRY_MARGIN_SECONDS = 60


class AuthError(Exception):
    pass


class IOLAuth:
    """Maneja el login y el refresco automático del bearer token de IOL."""

    def __init__(self, base_url, username, password, session=None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = session or requests.Session()

        self._access_token = None
        self._refresh_token = None
        self._expires_at = 0.0

    def get_token(self):
        if self._access_token is None:
            self._login()
        elif time.time() >= self._expires_at:
            self._refresh()
        return self._access_token

    def force_refresh(self):
        if self._refresh_token:
            self._refresh()
        else:
            self._login()

    def _login(self):
        logger.info("Autenticando contra %s", self.base_url)
        response = self.session.post(
            f"{self.base_url}/token",
            data={
                "username": self.username,
                "password": self.password,
                "grant_type": "password",
            },
            timeout=15,
        )
        self._handle_token_response(response)

    def _refresh(self):
        logger.info("Refrescando token")
        response = self.session.post(
            f"{self.base_url}/token",
            data={
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        if response.status_code != 200:
            # El refresh token también puede haber expirado (~7200s); reintentar con login pleno.
            logger.warning("Refresh falló (%s), reintentando login completo", response.status_code)
            self._login()
            return
        self._handle_token_response(response)

    def _handle_token_response(self, response):
        if response.status_code != 200:
            raise AuthError(
                f"No se pudo autenticar contra IOL (status {response.status_code}): {response.text[:200]}"
            )
        data = response.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token")
        expires_in = float(data.get("expires_in", 900))
        self._expires_at = time.time() + expires_in - _EXPIRY_MARGIN_SECONDS

    def auth_header(self):
        return {"Authorization": f"Bearer {self.get_token()}"}
