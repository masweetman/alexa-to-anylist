"""Tests for sync scheduling — _apply_schedule, /api/schedule/status,
and the sync_interval_minutes field in POST /settings."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import app as flask_app
import db


# ── _apply_schedule ───────────────────────────────────────────────────────────

class TestApplySchedule:
    def test_zero_interval_does_not_add_job(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs") as mock_remove, \
             patch.object(flask_app._scheduler, "add_job") as mock_add:
            flask_app._apply_schedule(0)
        mock_remove.assert_called_once()
        mock_add.assert_not_called()

    def test_negative_interval_does_not_add_job(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs"), \
             patch.object(flask_app._scheduler, "add_job") as mock_add:
            flask_app._apply_schedule(-1)
        mock_add.assert_not_called()

    def test_positive_interval_adds_job(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs"), \
             patch.object(flask_app._scheduler, "add_job") as mock_add:
            flask_app._apply_schedule(10)
        mock_add.assert_called_once()

    def test_job_interval_minutes_is_correct(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs"), \
             patch.object(flask_app._scheduler, "add_job") as mock_add:
            flask_app._apply_schedule(25)
        _, kwargs = mock_add.call_args
        assert kwargs["minutes"] == 25

    def test_job_target_is_run_sync(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs"), \
             patch.object(flask_app._scheduler, "add_job") as mock_add:
            flask_app._apply_schedule(5)
        args, _ = mock_add.call_args
        assert args[0] is flask_app._run_sync

    def test_uses_interval_trigger(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs"), \
             patch.object(flask_app._scheduler, "add_job") as mock_add:
            flask_app._apply_schedule(10)
        args, _ = mock_add.call_args
        assert args[1] == "interval"

    def test_always_clears_existing_jobs_first(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs") as mock_remove, \
             patch.object(flask_app._scheduler, "add_job"):
            flask_app._apply_schedule(10)
        mock_remove.assert_called_once()

    def test_clears_jobs_even_when_disabling(self):
        with patch.object(flask_app._scheduler, "remove_all_jobs") as mock_remove, \
             patch.object(flask_app._scheduler, "add_job"):
            flask_app._apply_schedule(0)
        mock_remove.assert_called_once()


# ── GET /api/schedule/status ──────────────────────────────────────────────────

class TestScheduleStatusRoute:
    def test_returns_200(self, client):
        with patch.object(flask_app._scheduler, "get_job", return_value=None):
            assert client.get("/api/schedule/status").status_code == 200

    def test_returns_json_with_required_keys(self, client):
        with patch.object(flask_app._scheduler, "get_job", return_value=None):
            data = client.get("/api/schedule/status").get_json()
        assert "enabled" in data
        assert "interval_minutes" in data
        assert "next_run" in data

    def test_enabled_false_when_no_job(self, client):
        with patch.object(flask_app._scheduler, "get_job", return_value=None):
            data = client.get("/api/schedule/status").get_json()
        assert data["enabled"] is False

    def test_interval_minutes_zero_when_not_configured(self, client):
        with patch.object(flask_app._scheduler, "get_job", return_value=None):
            data = client.get("/api/schedule/status").get_json()
        assert data["interval_minutes"] == 0

    def test_interval_minutes_from_db(self, client):
        db.set_setting("sync_interval_minutes", "15")
        with patch.object(flask_app._scheduler, "get_job", return_value=None):
            data = client.get("/api/schedule/status").get_json()
        assert data["interval_minutes"] == 15

    def test_next_run_none_when_no_job(self, client):
        with patch.object(flask_app._scheduler, "get_job", return_value=None):
            data = client.get("/api/schedule/status").get_json()
        assert data["next_run"] is None

    def test_enabled_true_when_job_exists(self, client):
        mock_job = MagicMock()
        mock_job.next_run_time = None
        with patch.object(flask_app._scheduler, "get_job", return_value=mock_job):
            data = client.get("/api/schedule/status").get_json()
        assert data["enabled"] is True

    def test_next_run_is_null_when_job_has_no_next_run_time(self, client):
        mock_job = MagicMock()
        mock_job.next_run_time = None
        with patch.object(flask_app._scheduler, "get_job", return_value=mock_job):
            data = client.get("/api/schedule/status").get_json()
        assert data["next_run"] is None

    def test_next_run_is_iso_string_when_job_has_next_run_time(self, client):
        mock_job = MagicMock()
        mock_job.next_run_time = datetime(2026, 3, 12, 10, 0, 0, tzinfo=timezone.utc)
        with patch.object(flask_app._scheduler, "get_job", return_value=mock_job):
            data = client.get("/api/schedule/status").get_json()
        assert data["next_run"] is not None
        assert "2026" in data["next_run"]


# ── sync_interval_minutes in POST /settings ───────────────────────────────────

class TestSettingsIntervalField:
    def test_saves_valid_interval(self, client):
        with patch("app._apply_schedule"):
            client.post("/settings", data={"sync_interval_minutes": "10"})
        assert db.get_setting("sync_interval_minutes") == "10"

    def test_saves_zero_to_disable(self, client):
        with patch("app._apply_schedule"):
            client.post("/settings", data={"sync_interval_minutes": "0"})
        assert db.get_setting("sync_interval_minutes") == "0"

    def test_calls_apply_schedule_with_correct_value(self, client):
        with patch("app._apply_schedule") as mock_apply:
            client.post("/settings", data={"sync_interval_minutes": "30"})
        mock_apply.assert_called_once_with(30)

    def test_calls_apply_schedule_with_zero_to_disable(self, client):
        with patch("app._apply_schedule") as mock_apply:
            client.post("/settings", data={"sync_interval_minutes": "0"})
        mock_apply.assert_called_once_with(0)

    def test_blank_field_does_not_overwrite_existing_interval(self, client):
        db.set_setting("sync_interval_minutes", "20")
        with patch("app._apply_schedule") as mock_apply:
            client.post("/settings", data={"sync_interval_minutes": ""})
        assert db.get_setting("sync_interval_minutes") == "20"
        mock_apply.assert_not_called()

    def test_rejects_negative_interval(self, client):
        with patch("app._apply_schedule") as mock_apply:
            client.post("/settings", data={"sync_interval_minutes": "-5"})
        assert db.get_setting("sync_interval_minutes") is None
        mock_apply.assert_not_called()

    def test_rejects_non_integer_string(self, client):
        with patch("app._apply_schedule") as mock_apply:
            client.post("/settings", data={"sync_interval_minutes": "abc"})
        assert db.get_setting("sync_interval_minutes") is None
        mock_apply.assert_not_called()

    def test_rejects_float_string(self, client):
        with patch("app._apply_schedule") as mock_apply:
            client.post("/settings", data={"sync_interval_minutes": "1.5"})
        assert db.get_setting("sync_interval_minutes") is None
        mock_apply.assert_not_called()

    def test_flash_error_on_invalid_interval(self, client):
        resp = client.post(
            "/settings",
            data={"sync_interval_minutes": "bad"},
            follow_redirects=True,
        )
        assert b"interval" in resp.data.lower()

    def test_flash_success_on_valid_interval(self, client):
        with patch("app._apply_schedule"):
            resp = client.post(
                "/settings",
                data={"sync_interval_minutes": "10"},
                follow_redirects=True,
            )
        assert b"Settings saved" in resp.data

    def test_does_not_call_apply_schedule_on_invalid_interval(self, client):
        with patch("app._apply_schedule") as mock_apply:
            client.post("/settings", data={"sync_interval_minutes": "bad"})
        mock_apply.assert_not_called()

    def test_large_valid_interval_is_accepted(self, client):
        with patch("app._apply_schedule"):
            client.post("/settings", data={"sync_interval_minutes": "1440"})
        assert db.get_setting("sync_interval_minutes") == "1440"


# ── GET /settings renders interval ────────────────────────────────────────────

class TestSettingsPageInterval:
    def test_shows_interval_input_field(self, client):
        resp = client.get("/settings")
        assert b"sync_interval_minutes" in resp.data

    def test_shows_saved_interval_value(self, client):
        db.set_setting("sync_interval_minutes", "20")
        resp = client.get("/settings")
        assert b"20" in resp.data

    def test_default_value_is_zero(self, client):
        resp = client.get("/settings")
        assert b'value="0"' in resp.data
