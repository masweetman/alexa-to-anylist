"""Unit tests for db.py — settings storage and sync log."""
import sqlite3

import db


# ── init_db ───────────────────────────────────────────────────────────────────

class TestInitDb:
    def test_creates_settings_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
        db.init_db()
        conn = sqlite3.connect(tmp_path / "fresh.db")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "settings" in tables

    def test_creates_sync_log_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "fresh.db")
        db.init_db()
        conn = sqlite3.connect(tmp_path / "fresh.db")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "sync_log" in tables

    def test_is_idempotent(self):
        """Calling init_db a second time must not raise or corrupt data."""
        db.set_setting("existing", "value")
        db.init_db()
        assert db.get_setting("existing") == "value"


# ── get_setting / set_setting ─────────────────────────────────────────────────

class TestGetSetSetting:
    def test_missing_key_returns_none(self):
        assert db.get_setting("no_such_key") is None

    def test_missing_key_returns_custom_default(self):
        assert db.get_setting("no_such_key", "fallback") == "fallback"

    def test_roundtrip(self):
        db.set_setting("k", "v")
        assert db.get_setting("k") == "v"

    def test_overwrite_existing_key(self):
        db.set_setting("k", "first")
        db.set_setting("k", "second")
        assert db.get_setting("k") == "second"

    def test_empty_string_value_stored_and_retrieved(self):
        db.set_setting("empty", "")
        assert db.get_setting("empty") == ""

    def test_whitespace_value_preserved(self):
        db.set_setting("ws", "  spaces  ")
        assert db.get_setting("ws") == "  spaces  "

    def test_unicode_value(self):
        db.set_setting("emoji", "🛒 café résumé")
        assert db.get_setting("emoji") == "🛒 café résumé"

    def test_large_value(self):
        big = "x" * 100_000
        db.set_setting("big", big)
        assert db.get_setting("big") == big

    def test_multiple_keys_are_independent(self):
        db.set_setting("a", "1")
        db.set_setting("b", "2")
        assert db.get_setting("a") == "1"
        assert db.get_setting("b") == "2"

    def test_sql_injection_in_value_is_safe(self):
        payload = "'; DROP TABLE settings; --"
        db.set_setting("evil", payload)
        assert db.get_setting("evil") == payload
        # Table must still exist
        assert db.get_all_settings() is not None

    def test_sql_injection_in_key_is_safe(self):
        key = "'; DROP TABLE settings; --"
        db.set_setting(key, "value")
        assert db.get_setting(key) == "value"

    def test_newlines_in_value(self):
        db.set_setting("ml", "line1\nline2\nline3")
        assert db.get_setting("ml") == "line1\nline2\nline3"

    def test_json_string_as_value(self):
        import json
        data = json.dumps([{"name": "session-id", "value": "abc"}])
        db.set_setting("cookies", data)
        assert json.loads(db.get_setting("cookies")) == [{"name": "session-id", "value": "abc"}]


# ── get_all_settings ──────────────────────────────────────────────────────────

class TestGetAllSettings:
    def test_empty_db_returns_empty_dict(self):
        assert db.get_all_settings() == {}

    def test_returns_all_stored_keys(self):
        db.set_setting("x", "1")
        db.set_setting("y", "2")
        result = db.get_all_settings()
        assert result == {"x": "1", "y": "2"}

    def test_overwritten_key_appears_once(self):
        db.set_setting("k", "v1")
        db.set_setting("k", "v2")
        result = db.get_all_settings()
        assert list(result.keys()).count("k") == 1
        assert result["k"] == "v2"


# ── add_log / get_recent_logs ─────────────────────────────────────────────────

class TestLogs:
    def test_add_and_retrieve(self):
        db.add_log("INFO", "hello world")
        logs = db.get_recent_logs()
        assert len(logs) == 1
        assert logs[0]["level"] == "INFO"
        assert logs[0]["message"] == "hello world"

    def test_timestamp_is_present_and_non_empty(self):
        db.add_log("INFO", "msg")
        logs = db.get_recent_logs()
        assert "timestamp" in logs[0]
        assert logs[0]["timestamp"]

    def test_most_recent_entry_is_first(self):
        db.add_log("INFO", "first")
        db.add_log("INFO", "second")
        db.add_log("INFO", "third")
        logs = db.get_recent_logs()
        assert logs[0]["message"] == "third"
        assert logs[-1]["message"] == "first"

    def test_limit_is_respected(self):
        for i in range(10):
            db.add_log("INFO", f"msg {i}")
        assert len(db.get_recent_logs(limit=3)) == 3

    def test_default_limit_is_100(self):
        for i in range(120):
            db.add_log("INFO", f"msg {i}")
        assert len(db.get_recent_logs()) == 100

    def test_error_level_stored_correctly(self):
        db.add_log("ERROR", "something broke")
        logs = db.get_recent_logs()
        assert logs[0]["level"] == "ERROR"

    def test_warning_level_stored_correctly(self):
        db.add_log("WARNING", "heads up")
        assert db.get_recent_logs()[0]["level"] == "WARNING"

    def test_empty_table_returns_empty_list(self):
        assert db.get_recent_logs() == []

    def test_each_entry_has_id(self):
        db.add_log("INFO", "hi")
        assert "id" in db.get_recent_logs()[0]

    def test_ids_are_integers(self):
        db.add_log("INFO", "hi")
        assert isinstance(db.get_recent_logs()[0]["id"], int)

    def test_sql_injection_in_message_is_safe(self):
        db.add_log("INFO", "'; DROP TABLE sync_log; --")
        logs = db.get_recent_logs()
        assert "DROP TABLE" in logs[0]["message"]

    def test_unicode_in_message(self):
        db.add_log("INFO", "synced: 🥛 Milk, 🥚 Eggs")
        assert "🥛" in db.get_recent_logs()[0]["message"]

    def test_limit_zero_returns_empty(self):
        db.add_log("INFO", "hi")
        assert db.get_recent_logs(limit=0) == []


# ── clear_logs ────────────────────────────────────────────────────────────────

class TestClearLogs:
    def test_clears_all_entries(self):
        db.add_log("INFO", "a")
        db.add_log("INFO", "b")
        db.clear_logs()
        assert db.get_recent_logs() == []

    def test_clear_on_empty_table_is_safe(self):
        db.clear_logs()  # must not raise
        assert db.get_recent_logs() == []

    def test_new_entries_can_be_added_after_clear(self):
        db.add_log("INFO", "old")
        db.clear_logs()
        db.add_log("INFO", "new")
        logs = db.get_recent_logs()
        assert len(logs) == 1
        assert logs[0]["message"] == "new"

    def test_settings_unaffected_by_clear_logs(self):
        db.set_setting("key", "val")
        db.add_log("INFO", "msg")
        db.clear_logs()
        assert db.get_setting("key") == "val"


# ── reset_credentials ─────────────────────────────────────────────────────────

class TestResetCredentials:
    def test_clears_site_password_hash(self):
        db.set_setting("site_password_hash", "somehash")
        db.reset_credentials()
        assert db.get_setting("site_password_hash") is None

    def test_clears_anylist_email(self):
        db.set_setting("anylist_email", "user@example.com")
        db.reset_credentials()
        assert db.get_setting("anylist_email") is None

    def test_clears_anylist_password(self):
        db.set_setting("anylist_password", "secret")
        db.reset_credentials()
        assert db.get_setting("anylist_password") is None

    def test_clears_amazon_cookies(self):
        db.set_setting("amazon_cookies", '[{"name":"x","value":"y"}]')
        db.reset_credentials()
        assert db.get_setting("amazon_cookies") is None

    def test_clears_cookies_updated_at(self):
        db.set_setting("cookies_updated_at", "2026-01-01 10:00 UTC")
        db.reset_credentials()
        assert db.get_setting("cookies_updated_at") is None

    def test_preserves_non_credential_settings(self):
        db.set_setting("anylist_list_name", "Groceries")
        db.set_setting("sync_interval_minutes", "10")
        db.reset_credentials()
        assert db.get_setting("anylist_list_name") == "Groceries"
        assert db.get_setting("sync_interval_minutes") == "10"

    def test_safe_when_no_credentials_stored(self):
        db.reset_credentials()  # must not raise
        assert db.get_setting("site_password_hash") is None

    def test_clears_all_credential_keys_at_once(self):
        db.set_setting("site_password_hash", "h")
        db.set_setting("anylist_email", "e@x.com")
        db.set_setting("anylist_password", "p")
        db.set_setting("amazon_cookies", "[]")
        db.set_setting("cookies_updated_at", "t")
        db.reset_credentials()
        settings = db.get_all_settings()
        credential_keys = {"site_password_hash", "anylist_email", "anylist_password",
                           "amazon_cookies", "cookies_updated_at"}
        assert credential_keys.isdisjoint(settings.keys())
