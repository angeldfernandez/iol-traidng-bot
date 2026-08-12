import time

import responses

from iol_bot.auth import AuthError, IOLAuth

BASE_URL = "https://api.invertironline.com"


@responses.activate
def test_login_success_stores_token():
    responses.add(
        responses.POST,
        f"{BASE_URL}/token",
        json={"access_token": "tok1", "refresh_token": "ref1", "expires_in": 900},
        status=200,
    )
    auth = IOLAuth(BASE_URL, "user", "pass")
    assert auth.get_token() == "tok1"
    assert auth.auth_header() == {"Authorization": "Bearer tok1"}


@responses.activate
def test_login_failure_raises_autherror():
    responses.add(responses.POST, f"{BASE_URL}/token", json={"error": "invalid"}, status=400)
    auth = IOLAuth(BASE_URL, "user", "wrongpass")
    try:
        auth.get_token()
        assert False, "debería haber lanzado AuthError"
    except AuthError:
        pass


@responses.activate
def test_token_is_refreshed_when_expired():
    responses.add(
        responses.POST,
        f"{BASE_URL}/token",
        json={"access_token": "tok1", "refresh_token": "ref1", "expires_in": 900},
        status=200,
    )
    auth = IOLAuth(BASE_URL, "user", "pass")
    assert auth.get_token() == "tok1"

    # Forzamos expiración
    auth._expires_at = time.time() - 1

    responses.add(
        responses.POST,
        f"{BASE_URL}/token",
        json={"access_token": "tok2", "refresh_token": "ref2", "expires_in": 900},
        status=200,
    )
    assert auth.get_token() == "tok2"

    refresh_calls = [c for c in responses.calls if "refresh_token" in (c.request.body or "")]
    assert len(refresh_calls) == 1


@responses.activate
def test_refresh_falls_back_to_login_if_refresh_token_expired():
    responses.add(
        responses.POST,
        f"{BASE_URL}/token",
        json={"access_token": "tok1", "refresh_token": "ref1", "expires_in": 900},
        status=200,
    )
    auth = IOLAuth(BASE_URL, "user", "pass")
    auth.get_token()
    auth._expires_at = time.time() - 1

    responses.add(responses.POST, f"{BASE_URL}/token", json={"error": "expired"}, status=400)
    responses.add(
        responses.POST,
        f"{BASE_URL}/token",
        json={"access_token": "tok3", "refresh_token": "ref3", "expires_in": 900},
        status=200,
    )
    assert auth.get_token() == "tok3"
