"""Tre fix di sicurezza reimplementati dall'upstream nesquena/hermes-webui.

1. #4171 — l'enrollment di una passkey e' un'azione di autenticazione: richiede
   una sessione valida; con auth disattivata passa solo dal gate locale del
   primo avvio. Prima: bastava raggiungere la WebUI in LAN per registrare una
   passkey e diventare amministratore.
2. #3982/#3991 — GET /api/session?session_id=... e /api/session/export
   restituivano qualsiasi sessione per id, anche di un altro profilo. Ora
   valgono le stesse regole di /api/sessions: profilo diverso = 404.
3. #4544 — un worker in background con profilo nominato ereditava dal processo
   server le credenziali non definite nel profilo (ANTHROPIC_TOKEN,
   CLAUDE_CODE_OAUTH_TOKEN, chiavi API): un profilo "vuoto" usava il token del
   profilo principale. Ora le credenziali assenti dal profilo vengono tolte
   dall'ambiente per la durata del worker.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from api import auth as auth_mod
from api import profiles, routes

REPO = Path(__file__).resolve().parents[1]


class _Handler:
    def __init__(self, cookie=None, client=("203.0.113.9", 4444)):
        self.wfile = io.BytesIO()
        self.status = None
        self.headers = {"Cookie": cookie} if cookie else {}
        self.client_address = client

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        pass

    def end_headers(self):
        pass


def _payload(h):
    return json.loads(h.wfile.getvalue().decode("utf-8"))


# ── 1. passkey enrollment ────────────────────────────────────────────────────

def test_passkey_enrollment_requires_session_when_auth_enabled(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth_mod, "parse_cookie", lambda handler: None)
    ok, error, status = routes._require_passkey_registration_auth(_Handler())
    assert (ok, status) == (False, 401)
    assert "Authentication required" in error


def test_passkey_enrollment_accepts_valid_session(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth_mod, "parse_cookie", lambda handler: "cookie-ok")
    monkeypatch.setattr(auth_mod, "verify_session", lambda value: value == "cookie-ok")
    assert routes._require_passkey_registration_auth(_Handler(cookie="hermes_session=cookie-ok")) == (True, "", 200)


def test_passkey_enrollment_without_auth_goes_through_the_local_gate(monkeypatch):
    monkeypatch.setattr(auth_mod, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(routes, "_onboarding_gate_allows", lambda handler: False)
    assert routes._require_passkey_registration_auth(_Handler())[2] == 401
    monkeypatch.setattr(routes, "_onboarding_gate_allows", lambda handler: True)
    assert routes._require_passkey_registration_auth(_Handler())[0] is True


def test_both_register_endpoints_call_the_gate():
    src = (REPO / "api" / "routes.py").read_text(encoding="utf-8")
    for path in ('"/api/auth/passkey/register/options"', '"/api/auth/passkey/register"'):
        start = src.index(f"if parsed.path == {path}:")
        block = src[start:start + 900]
        assert "_require_passkey_registration_auth(handler)" in block, path


# ── 2. sessione per id e export scoperti al profilo ─────────────────────────

def test_session_visibility_follows_active_profile(monkeypatch):
    monkeypatch.setattr(routes, "_get_active_profile_name", lambda: "giorgio")
    assert routes._session_visible_to_active_profile("giorgio", _Handler()) is True
    assert routes._session_visible_to_active_profile("tom", _Handler()) is False
    assert routes._session_visible_to_active_profile("tom", None) is True, "chiamate interne senza request: comportamento storico"


def test_export_of_another_profiles_session_is_404(monkeypatch):
    monkeypatch.setattr(routes, "_get_active_profile_name", lambda: "giorgio")
    monkeypatch.setattr(routes, "get_session", lambda sid, **kw: SimpleNamespace(profile="tom", id=sid, messages=[]))
    h = _Handler()
    routes._handle_session_export(h, urlparse("/api/session/export?session_id=abc"))
    assert h.status == 404
    assert "not found" in _payload(h)["error"].lower()


def test_export_of_own_session_still_works(monkeypatch):
    monkeypatch.setattr(routes, "_get_active_profile_name", lambda: "giorgio")
    monkeypatch.setattr(routes, "get_session", lambda sid, **kw: SimpleNamespace(profile="giorgio", id=sid, messages=[]))
    monkeypatch.setattr(routes, "redact_session_data", lambda d: {"id": d.get("id")})
    h = _Handler()
    routes._handle_session_export(h, urlparse("/api/session/export?session_id=abc"))
    assert h.status == 200
    assert _payload(h) == {"id": "abc"}


def test_get_session_by_id_checks_profile_in_source():
    src = (REPO / "api" / "routes.py").read_text(encoding="utf-8")
    start = src.index('if parsed.path == "/api/session":')
    block = src[start:start + 6000]
    assert "_session_visible_to_active_profile(_session_profile, handler)" in block


# ── 3. credenziali non del profilo tolte dall'ambiente del worker ───────────

def test_credential_env_names_cover_webui_map_and_agent_registry(monkeypatch):
    monkeypatch.setattr(profiles, "_agent_registry_credential_env_names", lambda: {"ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"})
    names = profiles.profile_credential_env_names()
    assert {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"} <= names


def test_background_worker_does_not_inherit_server_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(profiles, "_agent_registry_credential_env_names", lambda: {"ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"})
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda name: tmp_path / name)
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda home: {"OPENAI_API_KEY": "profile-key"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "server-oauth")
    monkeypatch.setenv("OPENAI_API_KEY", "server-openai")
    monkeypatch.setenv("NOTION_TOKEN", "not-a-provider-credential")

    with profiles.profile_env_for_background_worker("tom"):
        assert "ANTHROPIC_API_KEY" not in os.environ, "credenziale del server non definita nel profilo"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
        assert os.environ["OPENAI_API_KEY"] == "profile-key", "quella del profilo si'"
        assert os.environ["NOTION_TOKEN"] == "not-a-provider-credential", "non e' una credenziale provider: resta"

    assert os.environ["ANTHROPIC_API_KEY"] == "server-key", "ripristinata a fine worker"
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "server-oauth"
    assert os.environ["OPENAI_API_KEY"] == "server-openai"


def test_default_profile_keeps_environment_untouched(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-key")
    with profiles.profile_env_for_background_worker("default"):
        assert os.environ["ANTHROPIC_API_KEY"] == "server-key"
