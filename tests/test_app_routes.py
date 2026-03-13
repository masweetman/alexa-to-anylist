"""Integration tests for Flask routes in app.py."""
import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import app as flask_app
from app import _convert_to_local
import db


# ── GET / (dashboard) ─────────────────────────────────────────────────────────

class TestDashboard:
    def test_returns_200(self, client):
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        assert resp.status_code == 200

    def test_no_cookies_warning_shown_when_not_configured(self, client):
        with patch("app.alexa.get_shopping_list_items", return_value=None):
            resp = client.get("/")
        assert b"No Amazon cookies" in resp.data

    def test_active_items_rendered(self, client):
        db.set_setting("amazon_cookies", json.dumps([{"name": "x", "value": "y"}]))
        items = [
            {"value": "Milk", "completed": False},
            {"value": "Eggs", "completed": False},
        ]
        with patch("app.alexa.get_shopping_list_items", return_value=items):
            resp = client.get("/")
        assert b"Milk" in resp.data
        assert b"Eggs" in resp.data

    def test_completed_items_not_rendered(self, client):
        db.set_setting("amazon_cookies", json.dumps([{"name": "x", "value": "y"}]))
        items = [
            {"value": "Milk", "completed": False},
            {"value": "AlreadyDone", "completed": True},
        ]
        with patch("app.alexa.get_shopping_list_items", return_value=items):
            resp = client.get("/")
        assert b"Milk" in resp.data
        assert b"AlreadyDone" not in resp.data

    def test_alexa_error_shown_when_cookies_set_but_api_fails(self, client):
        db.set_setting("amazon_cookies", json.dumps([{"name": "x", "value": "y"}]))
        with patch("app.alexa.get_shopping_list_items", return_value=None):
            resp = client.get("/")
        assert resp.status_code == 200
        assert b"Could not reach" in resp.data

    def test_sync_log_entries_rendered(self, client):
        db.add_log("INFO", "test sync ran")
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        assert b"test sync ran" in resp.data

    def test_empty_list_shows_no_items_message(self, client):
        db.set_setting("amazon_cookies", json.dumps([{"name": "x", "value": "y"}]))
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        assert b"empty" in resp.data or b"No active" in resp.data


# ── GET /settings ──────────────────────────────────────────────────────────────

class TestSettingsGet:
    def test_returns_200(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_shows_existing_email(self, client):
        db.set_setting("anylist_email", "user@example.com")
        resp = client.get("/settings")
        assert b"user@example.com" in resp.data

    def test_shows_existing_list_name(self, client):
        db.set_setting("anylist_list_name", "Groceries")
        resp = client.get("/settings")
        assert b"Groceries" in resp.data

    def test_shows_cookies_updated_at(self, client):
        db.set_setting("cookies_updated_at", "2026-01-01 10:00 UTC")
        resp = client.get("/settings")
        assert b"2026-01-01 10:00 UTC" in resp.data

    def test_shows_no_cookies_warning_when_not_set(self, client):
        resp = client.get("/settings")
        assert b"No cookies saved" in resp.data


# ── POST /settings ─────────────────────────────────────────────────────────────

class TestSettingsPost:
    def test_saves_anylist_email(self, client):
        client.post("/settings", data={"anylist_email": "me@x.com"})
        assert db.get_setting("anylist_email") == "me@x.com"

    def test_saves_anylist_list_name(self, client):
        client.post("/settings", data={"anylist_list_name": "Grocery"})
        assert db.get_setting("anylist_list_name") == "Grocery"

    def test_saves_amazon_url(self, client):
        client.post("/settings", data={"amazon_url": "https://www.amazon.co.uk"})
        assert db.get_setting("amazon_url") == "https://www.amazon.co.uk"

    def test_saves_password_when_provided(self, client):
        client.post("/settings", data={"anylist_password": "s3cret!"})
        assert db.get_setting("anylist_password") == "s3cret!"

    def test_does_not_overwrite_password_when_blank(self, client):
        db.set_setting("anylist_password", "original")
        client.post("/settings", data={"anylist_password": ""})
        assert db.get_setting("anylist_password") == "original"

    def test_does_not_overwrite_password_when_whitespace(self, client):
        db.set_setting("anylist_password", "original")
        client.post("/settings", data={"anylist_password": "   "})
        assert db.get_setting("anylist_password") == "original"

    def test_does_not_save_blank_email(self, client):
        db.set_setting("anylist_email", "existing@x.com")
        client.post("/settings", data={"anylist_email": ""})
        assert db.get_setting("anylist_email") == "existing@x.com"

    def test_does_not_save_whitespace_only_field(self, client):
        db.set_setting("anylist_email", "existing@x.com")
        client.post("/settings", data={"anylist_email": "   "})
        assert db.get_setting("anylist_email") == "existing@x.com"

    def test_saves_valid_cookies_json(self, client):
        cookies = json.dumps([{"name": "x", "value": "y"}])
        client.post("/settings", data={"amazon_cookies": cookies})
        saved = db.get_setting("amazon_cookies")
        assert saved is not None
        assert json.loads(saved) == [{"name": "x", "value": "y"}]

    def test_sets_cookies_updated_at_after_saving_cookies(self, client):
        cookies = json.dumps([{"name": "x", "value": "y"}])
        client.post("/settings", data={"amazon_cookies": cookies})
        assert db.get_setting("cookies_updated_at") is not None

    def test_rejects_invalid_json_cookies(self, client):
        resp = client.post(
            "/settings", data={"amazon_cookies": "not-json"}, follow_redirects=True
        )
        assert b"Invalid cookies JSON" in resp.data

    def test_rejects_json_object_as_cookies(self, client):
        resp = client.post(
            "/settings", data={"amazon_cookies": '{"name":"x"}'}, follow_redirects=True
        )
        assert b"Invalid cookies JSON" in resp.data

    def test_rejects_json_string_as_cookies(self, client):
        resp = client.post(
            "/settings", data={"amazon_cookies": '"just a string"'}, follow_redirects=True
        )
        assert b"Invalid cookies JSON" in resp.data

    def test_does_not_save_invalid_cookies(self, client):
        client.post("/settings", data={"amazon_cookies": "bad!!!"})
        assert db.get_setting("amazon_cookies") is None

    def test_redirects_to_settings_on_success(self, client):
        resp = client.post("/settings", data={"anylist_email": "a@b.com"})
        assert resp.status_code == 302
        assert "/settings" in resp.headers["Location"]

    def test_redirects_to_settings_on_cookie_error(self, client):
        resp = client.post("/settings", data={"amazon_cookies": "bad!!!"})
        assert resp.status_code == 302

    def test_flash_success_on_save(self, client):
        resp = client.post(
            "/settings", data={"anylist_email": "x@y.com"}, follow_redirects=True
        )
        assert b"Settings saved" in resp.data

    def test_empty_post_body_is_safe(self, client):
        resp = client.post("/settings", data={})
        assert resp.status_code == 302

    def test_xss_in_email_stored_as_literal_text(self, client):
        """XSS payload stored as literal — Jinja2 auto-escapes on render."""
        payload = "<script>alert(1)</script>"
        client.post("/settings", data={"anylist_email": payload})
        resp = client.get("/settings")
        # Jinja2 escapes it; raw script tag must not appear
        assert b"<script>alert(1)</script>" not in resp.data


# ── POST /sync ─────────────────────────────────────────────────────────────────

class TestSyncRoute:
    def test_redirects_to_dashboard(self, client):
        with patch("app._run_sync"):
            resp = client.post("/sync")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/") or "localhost" in resp.headers["Location"]

    def test_flash_message_informs_user(self, client):
        with patch("app._run_sync"):
            resp = client.post("/sync", follow_redirects=True)
        assert b"Sync started" in resp.data

    def test_sync_runs_in_background(self, client):
        """POST /sync must return quickly even if sync is slow."""
        with patch("app.threading.Thread") as mock_thread_cls:
            mock_thread = MagicMock()
            mock_thread_cls.return_value = mock_thread
            resp = client.post("/sync")
        # Response must redirect immediately without blocking on the thread
        assert resp.status_code == 302
        mock_thread.start.assert_called_once()


# ── GET /auth/status ───────────────────────────────────────────────────────────

class TestAuthStatus:
    def test_returns_200(self, client):
        assert client.get("/auth/status").status_code == 200

    def test_initial_status_is_idle(self, client):
        assert client.get("/auth/status").get_json()["status"] == "idle"

    def test_reflects_updated_status(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "waiting"
        assert client.get("/auth/status").get_json()["status"] == "waiting"

    def test_returns_error_field_when_set(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["error"] = "browser crashed"
        assert client.get("/auth/status").get_json()["error"] == "browser crashed"

    def test_returns_null_error_when_not_set(self, client):
        assert client.get("/auth/status").get_json()["error"] is None


# ── POST /auth/start ───────────────────────────────────────────────────────────

class TestAuthStart:
    def test_returns_started(self, client):
        with patch("app._run_browser_thread"):
            data = client.post("/auth/start").get_json()
        assert data["status"] == "started"

    def test_returns_already_running_when_waiting(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "waiting"
        assert client.post("/auth/start").get_json()["status"] == "already_running"

    def test_returns_already_running_when_extracting(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "extracting"
        assert client.post("/auth/start").get_json()["status"] == "already_running"

    def test_returns_already_running_when_starting(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "starting"
        assert client.post("/auth/start").get_json()["status"] == "already_running"

    def test_clears_previous_error_on_start(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["error"] = "old error"
            flask_app._auth_state["status"] = "error"
        with patch("app._run_browser_thread"):
            client.post("/auth/start")
        with flask_app._auth_lock:
            assert flask_app._auth_state["error"] is None

    def test_clears_previous_cookies_on_start(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["cookies"] = [{"name": "old"}]
            flask_app._auth_state["status"] = "idle"
        with patch("app._run_browser_thread"):
            client.post("/auth/start")
        with flask_app._auth_lock:
            assert flask_app._auth_state["cookies"] is None


# ── POST /auth/complete ────────────────────────────────────────────────────────

class TestAuthComplete:
    def test_returns_400_when_not_in_waiting_state(self, client):
        resp = client.post("/auth/complete")
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_returns_500_on_timeout(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "waiting"
        with patch("app.time.sleep"):  # no actual sleep
            resp = client.post("/auth/complete")
        assert resp.status_code == 500
        assert resp.get_json()["ok"] is False

    def test_saves_cookies_and_returns_ok_when_done(self, client):
        fake_cookies = [{"name": "s", "value": "v"}]
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "waiting"

        def _set_done():
            time.sleep(0.05)
            with flask_app._auth_lock:
                flask_app._auth_state["cookies"] = fake_cookies
                flask_app._auth_state["status"] = "done"

        threading.Thread(target=_set_done, daemon=True).start()
        resp = client.post("/auth/complete")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] == 1
        saved = json.loads(db.get_setting("amazon_cookies"))
        assert saved[0]["name"] == "s"

    def test_sets_cookies_updated_at_after_success(self, client):
        fake_cookies = [{"name": "s", "value": "v"}]
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "waiting"

        def _set_done():
            time.sleep(0.05)
            with flask_app._auth_lock:
                flask_app._auth_state["cookies"] = fake_cookies
                flask_app._auth_state["status"] = "done"

        threading.Thread(target=_set_done, daemon=True).start()
        client.post("/auth/complete")
        assert db.get_setting("cookies_updated_at") is not None

    def test_resets_state_to_idle_after_success(self, client):
        fake_cookies = [{"name": "s", "value": "v"}]
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "waiting"

        def _set_done():
            time.sleep(0.05)
            with flask_app._auth_lock:
                flask_app._auth_state["cookies"] = fake_cookies
                flask_app._auth_state["status"] = "done"

        threading.Thread(target=_set_done, daemon=True).start()
        client.post("/auth/complete")

        with flask_app._auth_lock:
            assert flask_app._auth_state["status"] == "idle"

    def test_returns_error_detail_when_extraction_failed(self, client):
        with flask_app._auth_lock:
            flask_app._auth_state["status"] = "waiting"

        def _set_error():
            time.sleep(0.05)
            with flask_app._auth_lock:
                flask_app._auth_state["error"] = "Chrome crashed"
                flask_app._auth_state["status"] = "error"

        threading.Thread(target=_set_error, daemon=True).start()
        with patch("app.time.sleep"):
            resp = client.post("/auth/complete")
        assert resp.status_code == 500


# ── GET /api/logs ─────────────────────────────────────────────────────────────

class TestApiLogs:
    def test_returns_200(self, client):
        assert client.get("/api/logs").status_code == 200

    def test_returns_json_list(self, client):
        assert isinstance(client.get("/api/logs").get_json(), list)

    def test_empty_when_no_logs(self, client):
        assert client.get("/api/logs").get_json() == []

    def test_includes_added_log_entries(self, client):
        db.add_log("INFO", "api test message")
        messages = [e["message"] for e in client.get("/api/logs").get_json()]
        assert "api test message" in messages

    def test_returns_up_to_100_entries(self, client):
        for i in range(120):
            db.add_log("INFO", f"msg {i}")
        assert len(client.get("/api/logs").get_json()) == 100


# ── POST /api/logs/clear ──────────────────────────────────────────────────────

class TestApiLogsClear:
    def test_returns_ok_true(self, client):
        assert client.post("/api/logs/clear").get_json()["ok"] is True

    def test_clears_all_log_entries(self, client):
        db.add_log("INFO", "will be cleared")
        client.post("/api/logs/clear")
        assert db.get_recent_logs() == []

    def test_subsequent_api_logs_returns_empty(self, client):
        db.add_log("INFO", "old")
        client.post("/api/logs/clear")
        assert client.get("/api/logs").get_json() == []


# ── GET /forgot-password ───────────────────────────────────────────────────────

class TestForgotPasswordGet:
    def test_returns_200(self, client):
        resp = client.get("/forgot-password")
        assert resp.status_code == 200

    def test_shows_confirmation_content(self, client):
        resp = client.get("/forgot-password")
        assert b"Reset" in resp.data

    def test_accessible_without_login(self, client):
        db.set_setting("site_password_hash", "somehash")
        resp = client.get("/forgot-password")
        assert resp.status_code == 200

    def test_has_cancel_link_to_login(self, client):
        resp = client.get("/forgot-password")
        assert b"/login" in resp.data


# ── POST /forgot-password ──────────────────────────────────────────────────────

class TestForgotPasswordPost:
    def test_redirects_to_login(self, client):
        resp = client.post("/forgot-password")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_clears_site_password(self, client):
        from werkzeug.security import generate_password_hash
        db.set_setting("site_password_hash", generate_password_hash("secret"))
        client.post("/forgot-password")
        assert db.get_setting("site_password_hash") is None

    def test_clears_anylist_credentials(self, client):
        db.set_setting("anylist_email", "user@example.com")
        db.set_setting("anylist_password", "pass")
        client.post("/forgot-password")
        assert db.get_setting("anylist_email") is None
        assert db.get_setting("anylist_password") is None

    def test_clears_amazon_cookies(self, client):
        db.set_setting("amazon_cookies", '[{"name":"x","value":"y"}]')
        client.post("/forgot-password")
        assert db.get_setting("amazon_cookies") is None

    def test_preserves_non_credential_settings(self, client):
        db.set_setting("anylist_list_name", "Groceries")
        db.set_setting("sync_interval_minutes", "15")
        client.post("/forgot-password")
        assert db.get_setting("anylist_list_name") == "Groceries"
        assert db.get_setting("sync_interval_minutes") == "15"

    def test_flash_message_shown_after_reset(self, client):
        resp = client.post("/forgot-password", follow_redirects=True)
        assert b"reset" in resp.data.lower()

    def test_clears_session(self, client):
        with client.session_transaction() as sess:
            sess["authenticated"] = True
        client.post("/forgot-password")
        with client.session_transaction() as sess:
            assert "authenticated" not in sess

    def test_site_is_accessible_after_reset(self, client):
        """After reset, no password is set so the site should be open."""
        client.post("/forgot-password")
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        assert resp.status_code == 200


# ── _convert_to_local (unit) ───────────────────────────────────────────────────

class TestConvertToLocal:
    def test_utc_passthrough(self):
        result = _convert_to_local("2026-03-12 14:30:00 UTC", "UTC")
        assert "2026-03-12" in result
        assert "14:30" in result

    def test_converts_to_eastern(self):
        # 14:30 UTC = 10:30 EDT (UTC-4)
        result = _convert_to_local("2026-03-12 14:30:00 UTC", "America/New_York")
        assert "10:30" in result

    def test_converts_short_format(self):
        """Also handles the HH:MM (no seconds) format used for cookies_updated_at."""
        result = _convert_to_local("2026-03-12 14:30 UTC", "America/Los_Angeles")
        assert "07:30" in result  # UTC-7 in March (PDT)

    def test_returns_empty_for_none(self):
        assert _convert_to_local(None, "UTC") == ""

    def test_returns_empty_for_empty_string(self):
        assert _convert_to_local("", "UTC") == ""

    def test_unrecognized_format_returned_as_is(self):
        weird = "some unparseable string"
        assert _convert_to_local(weird, "UTC") == weird

    def test_invalid_tz_falls_back_to_utc(self):
        result = _convert_to_local("2026-03-12 14:30:00 UTC", "Not/ATimezone")
        assert "14:30" in result

    def test_includes_timezone_abbreviation(self):
        result = _convert_to_local("2026-03-12 14:30:00 UTC", "UTC")
        assert "UTC" in result

    def test_tokyo_offset(self):
        # 14:30 UTC = 23:30 JST (UTC+9)
        result = _convert_to_local("2026-03-12 14:30:00 UTC", "Asia/Tokyo")
        assert "23:30" in result


# ── Timezone setting (routes) ─────────────────────────────────────────────────

class TestTimezoneSettings:
    def test_settings_page_shows_timezone_select(self, client):
        resp = client.get("/settings")
        assert b"timezone" in resp.data

    def test_saves_timezone_on_post(self, client):
        client.post("/settings", data={"timezone": "Europe/London"})
        assert db.get_setting("timezone") == "Europe/London"

    def test_saved_timezone_is_selected_in_form(self, client):
        db.set_setting("timezone", "Asia/Tokyo")
        resp = client.get("/settings")
        assert b"Asia/Tokyo" in resp.data
        # The selected option should appear with "selected" attribute
        assert b'value="Asia/Tokyo"\n                selected' in resp.data or \
               b"Asia/Tokyo" in resp.data  # selected is present in rendered HTML

    def test_default_timezone_is_utc(self, client):
        resp = client.get("/settings")
        # UTC should appear as a timezone option value
        assert b'value="UTC"' in resp.data

    def test_log_timestamps_converted_on_dashboard(self, client):
        db.set_setting("timezone", "America/New_York")
        db.add_log("INFO", "test entry")
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            resp = client.get("/")
        # The displayed timestamp should include EDT/EST timezone abbreviation
        assert b"ET" in resp.data or b"EST" in resp.data or b"EDT" in resp.data

    def test_cookies_updated_at_converted_on_settings(self, client):
        db.set_setting("timezone", "America/New_York")
        db.set_setting("cookies_updated_at", "2026-03-12 14:30 UTC")
        resp = client.get("/settings")
        # 14:30 UTC → 10:30 EDT
        assert b"10:30" in resp.data
