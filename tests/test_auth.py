"""Tests for site password protection: require_login, /login, /logout,
and the site-password fields in POST /settings."""
import json
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

import app as flask_app
import db

CORRECT_PASSWORD = "hunter2"
WRONG_PASSWORD = "wrong"


def _set_password(password: str = CORRECT_PASSWORD) -> None:
    db.set_setting("site_password_hash", generate_password_hash(password))


# ── require_login (before_request) ────────────────────────────────────────────

class TestRequireLogin:
    def test_open_site_allows_access_without_login(self, client):
        # No password set — all pages accessible
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        assert resp.status_code == 200

    def test_protected_site_redirects_unauthenticated_request(self, client):
        _set_password()
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirect_preserves_next_url(self, client):
        _set_password()
        resp = client.get("/settings")
        location = resp.headers["Location"]
        assert "next=" in location and "settings" in location

    def test_authenticated_session_allows_access(self, client):
        _set_password()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        assert resp.status_code == 200

    def test_login_page_exempt_from_redirect(self, client):
        _set_password()
        resp = client.get("/login")
        # Must not redirect back to itself
        assert resp.status_code == 200

    def test_logout_exempt_from_redirect(self, client):
        _set_password()
        resp = client.post("/logout")
        # Redirects to login — not blocked by require_login
        assert resp.status_code == 302

    def test_empty_password_hash_is_treated_as_no_password(self, client):
        # set_setting with "" means the hash row exists but is falsy
        db.set_setting("site_password_hash", "")
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        assert resp.status_code == 200

    def test_all_protected_routes_redirect(self, client):
        _set_password()
        for method, path in [
            ("GET", "/"),
            ("GET", "/settings"),
            ("POST", "/sync"),
            ("GET", "/api/logs"),
            ("POST", "/api/logs/clear"),
            ("GET", "/auth/status"),
        ]:
            resp = getattr(client, method.lower())(path)
            assert resp.status_code == 302, f"{method} {path} should redirect"
            assert "/login" in resp.headers["Location"], \
                f"{method} {path} should redirect to /login"


# ── GET /login ────────────────────────────────────────────────────────────────

class TestLoginGet:
    def test_returns_200(self, client):
        assert client.get("/login").status_code == 200

    def test_renders_password_input(self, client):
        assert b'name="password"' in client.get("/login").data

    def test_next_url_embedded_in_form(self, client):
        _set_password()
        resp = client.get("/login?next=/settings")
        assert b"/settings" in resp.data

    def test_next_url_must_be_local_path(self, client):
        resp = client.get("/login?next=https://evil.com")
        assert b"https://evil.com" not in resp.data


# ── POST /login ───────────────────────────────────────────────────────────────

class TestLoginPost:
    def test_correct_password_sets_session(self, client):
        _set_password()
        client.post("/login", data={"password": CORRECT_PASSWORD})
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True

    def test_correct_password_redirects_to_dashboard(self, client):
        _set_password()
        resp = client.post("/login", data={"password": CORRECT_PASSWORD})
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")

    def test_correct_password_redirects_to_next_url(self, client):
        _set_password()
        resp = client.post(
            "/login", data={"password": CORRECT_PASSWORD, "next": "/settings"}
        )
        assert resp.status_code == 302
        assert "/settings" in resp.headers["Location"]

    def test_wrong_password_does_not_set_session(self, client):
        _set_password()
        client.post("/login", data={"password": WRONG_PASSWORD})
        with client.session_transaction() as sess:
            assert not sess.get("authenticated")

    def test_wrong_password_returns_200_with_error_message(self, client):
        _set_password()
        resp = client.post(
            "/login", data={"password": WRONG_PASSWORD}, follow_redirects=True
        )
        assert resp.status_code == 200
        assert b"Incorrect password" in resp.data

    def test_empty_password_is_rejected(self, client):
        _set_password()
        resp = client.post("/login", data={"password": ""}, follow_redirects=True)
        assert b"Incorrect password" in resp.data

    def test_login_with_no_site_password_set_does_not_authenticate(self, client):
        # No password configured — submitting the form should not grant access
        resp = client.post(
            "/login", data={"password": "anything"}, follow_redirects=True
        )
        with client.session_transaction() as sess:
            assert not sess.get("authenticated")

    def test_external_next_url_is_ignored(self, client):
        _set_password()
        resp = client.post(
            "/login",
            data={"password": CORRECT_PASSWORD, "next": "https://evil.com"},
        )
        assert resp.status_code == 302
        assert "evil.com" not in resp.headers["Location"]

    def test_relative_next_url_with_no_leading_slash_is_ignored(self, client):
        _set_password()
        resp = client.post(
            "/login",
            data={"password": CORRECT_PASSWORD, "next": "evil.com/steal"},
        )
        assert "evil.com" not in resp.headers["Location"]

    def test_password_is_verified_with_hash_not_plaintext(self, client):
        # Store the hash; plaintext must not be accepted as-is
        hashed = generate_password_hash(CORRECT_PASSWORD)
        db.set_setting("site_password_hash", hashed)
        # Submitting the raw hash as the password must fail
        resp = client.post("/login", data={"password": hashed}, follow_redirects=True)
        assert b"Incorrect password" in resp.data


# ── POST /logout ──────────────────────────────────────────────────────────────

class TestLogout:
    def test_clears_authenticated_session(self, client):
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        client.post("/logout")
        with client.session_transaction() as sess:
            assert not sess.get("authenticated")

    def test_redirects_to_login(self, client):
        resp = client.post("/logout")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_logout_is_idempotent_when_not_logged_in(self, client):
        resp = client.post("/logout")
        assert resp.status_code == 302

    def test_after_logout_protected_pages_redirect(self, client):
        _set_password()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        client.post("/logout")
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ── POST /settings — site password fields ─────────────────────────────────────

class TestSettingsSitePassword:
    def _authenticated_client(self, client):
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        return client

    def test_setting_site_password_saves_hash(self, client):
        self._authenticated_client(client)
        client.post("/settings", data={"site_password": "newpass"})
        assert db.get_setting("site_password_hash") is not None
        assert db.get_setting("site_password_hash") != "newpass"  # must be hashed

    def test_setting_site_password_hash_is_verifiable(self, client):
        from werkzeug.security import check_password_hash
        self._authenticated_client(client)
        client.post("/settings", data={"site_password": "newpass"})
        assert check_password_hash(db.get_setting("site_password_hash"), "newpass")

    def test_setting_password_keeps_session_authenticated(self, client):
        self._authenticated_client(client)
        client.post("/settings", data={"site_password": "newpass"})
        with client.session_transaction() as sess:
            assert sess.get("authenticated") is True

    def test_blank_site_password_field_does_not_change_existing_hash(self, client):
        self._authenticated_client(client)
        _set_password("original")
        original_hash = db.get_setting("site_password_hash")
        client.post("/settings", data={"site_password": ""})
        assert db.get_setting("site_password_hash") == original_hash

    def test_whitespace_site_password_is_ignored(self, client):
        self._authenticated_client(client)
        _set_password("original")
        original_hash = db.get_setting("site_password_hash")
        client.post("/settings", data={"site_password": "   "})
        assert db.get_setting("site_password_hash") == original_hash

    def test_clearing_password_removes_hash(self, client):
        self._authenticated_client(client)
        _set_password()
        client.post("/settings", data={"site_password_clear": "1"})
        assert not db.get_setting("site_password_hash")

    def test_clearing_password_logs_session_out(self, client):
        self._authenticated_client(client)
        _set_password()
        client.post("/settings", data={"site_password_clear": "1"})
        with client.session_transaction() as sess:
            assert not sess.get("authenticated")

    def test_clear_checkbox_ignored_when_no_password_set(self, client):
        self._authenticated_client(client)
        # No existing password — sending the checkbox must not crash
        resp = client.post("/settings", data={"site_password_clear": "1"})
        assert resp.status_code == 302

    def test_new_password_overrides_clear_checkbox(self, client):
        """If both a new password and the clear checkbox are sent, new password wins."""
        self._authenticated_client(client)
        _set_password("old")
        client.post(
            "/settings",
            data={"site_password": "newpass", "site_password_clear": "1"},
        )
        # New password should be saved (new password field takes precedence in settings_save)
        from werkzeug.security import check_password_hash
        assert check_password_hash(db.get_setting("site_password_hash"), "newpass")
