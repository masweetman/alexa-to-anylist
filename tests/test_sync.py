"""Unit tests for _run_sync() in app.py — the core sync logic."""
from unittest.mock import MagicMock, call, patch

import pytest

import app
import db


def _alexa_item(value, completed=False, item_id=None):
    return {"id": item_id or f"id-{value}", "value": value, "completed": completed}


def _anylist_item(name, is_checked=False, item_id=None):
    m = MagicMock()
    m.name = name
    m.is_checked = is_checked
    m.id = item_id or f"li-{name}"
    return m


def _make_client(list_name="Shopping List", list_id="lst-1", items=None):
    """Return a (mock_client, mock_list) pair with configurable items."""
    lst = MagicMock()
    lst.name = list_name
    lst.id = list_id
    lst.items = items or []
    client = MagicMock()
    client.get_list_by_name.return_value = lst
    client.get_lists.return_value = [lst]
    return client, lst


def _configure_db(email="a@b.com", password="pass", list_name="Shopping List"):
    db.set_setting("anylist_email", email)
    db.set_setting("anylist_password", password)
    db.set_setting("anylist_list_name", list_name)


# ── Alexa fetch failure ───────────────────────────────────────────────────────

class TestSyncAlexaFailure:
    def test_returns_false_when_alexa_returns_none(self):
        with patch("app.alexa.get_shopping_list_items", return_value=None):
            assert app._run_sync() is False

    def test_logs_error_when_alexa_fails(self):
        with patch("app.alexa.get_shopping_list_items", return_value=None):
            app._run_sync()
        assert any(e["level"] == "ERROR" for e in db.get_recent_logs())

    def test_does_not_call_anylist_when_alexa_fails(self):
        with patch("app.alexa.get_shopping_list_items", return_value=None), \
             patch("app.AnyListClient.login") as mock_login:
            app._run_sync()
        mock_login.assert_not_called()


# ── No active items ───────────────────────────────────────────────────────────

class TestSyncNoActiveItems:
    def test_returns_true_when_list_is_empty(self):
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            assert app._run_sync() is True

    def test_returns_true_when_all_items_completed(self):
        items = [_alexa_item("Milk", completed=True)]
        with patch("app.alexa.get_shopping_list_items", return_value=items):
            assert app._run_sync() is True

    def test_logs_nothing_to_sync(self):
        with patch("app.alexa.get_shopping_list_items", return_value=[]):
            app._run_sync()
        assert any("Nothing to sync" in e["message"] for e in db.get_recent_logs())

    def test_does_not_call_anylist_when_nothing_to_sync(self):
        with patch("app.alexa.get_shopping_list_items", return_value=[]), \
             patch("app.AnyListClient.login") as mock_login:
            app._run_sync()
        mock_login.assert_not_called()


# ── AnyList credentials missing ───────────────────────────────────────────────

class TestSyncMissingCredentials:
    def test_returns_false_when_no_email(self):
        db.set_setting("anylist_password", "pass")
        items = [_alexa_item("Milk")]
        with patch("app.alexa.get_shopping_list_items", return_value=items):
            assert app._run_sync() is False

    def test_returns_false_when_no_password(self):
        db.set_setting("anylist_email", "a@b.com")
        items = [_alexa_item("Milk")]
        with patch("app.alexa.get_shopping_list_items", return_value=items):
            assert app._run_sync() is False

    def test_returns_false_when_both_missing(self):
        items = [_alexa_item("Milk")]
        with patch("app.alexa.get_shopping_list_items", return_value=items):
            assert app._run_sync() is False

    def test_logs_credentials_error(self):
        items = [_alexa_item("Milk")]
        with patch("app.alexa.get_shopping_list_items", return_value=items):
            app._run_sync()
        assert any("credentials" in e["message"].lower() for e in db.get_recent_logs())

    def test_does_not_mark_alexa_items_complete(self):
        items = [_alexa_item("Milk")]
        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed") as mock_complete:
            app._run_sync()
        mock_complete.assert_not_called()


# ── AnyList list not found ────────────────────────────────────────────────────

class TestSyncListNotFound:
    def test_returns_false_when_list_not_found(self):
        _configure_db(list_name="Nonexistent")
        items = [_alexa_item("Milk")]
        client = MagicMock()
        client.get_list_by_name.return_value = None
        other = MagicMock()
        other.name = "Other List"
        client.get_lists.return_value = [other]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.AnyListClient.login", return_value=client):
            assert app._run_sync() is False

    def test_logs_available_lists_when_not_found(self):
        _configure_db(list_name="Missing")
        items = [_alexa_item("Milk")]
        client = MagicMock()
        client.get_list_by_name.return_value = None
        available = MagicMock()
        available.name = "Groceries"
        client.get_lists.return_value = [available]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert any("Groceries" in e["message"] for e in db.get_recent_logs())

    def test_does_not_mark_alexa_complete_when_list_not_found(self):
        _configure_db(list_name="Nonexistent")
        items = [_alexa_item("Milk")]
        client = MagicMock()
        client.get_list_by_name.return_value = None
        client.get_lists.return_value = []

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed") as mock_complete, \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()
        mock_complete.assert_not_called()


# ── Normal sync flow ──────────────────────────────────────────────────────────

class TestSyncNormalFlow:
    def test_adds_new_item(self):
        _configure_db()
        client, lst = _make_client()
        items = [_alexa_item("Milk")]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            result = app._run_sync()

        client.add_item.assert_called_once_with(lst.id, "Milk")
        assert result is True

    def test_adds_multiple_items(self):
        _configure_db()
        client, lst = _make_client()
        items = [_alexa_item(n) for n in ["Milk", "Eggs", "Bread", "Butter"]]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert client.add_item.call_count == 4

    def test_marks_all_alexa_items_complete_after_push(self):
        _configure_db()
        client, lst = _make_client()
        milk = _alexa_item("Milk", item_id="id-milk")
        eggs = _alexa_item("Eggs", item_id="id-eggs")

        with patch("app.alexa.get_shopping_list_items", return_value=[milk, eggs]), \
             patch("app.alexa.mark_item_completed") as mock_complete, \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert mock_complete.call_count == 2
        completed_ids = {c[0][0]["id"] for c in mock_complete.call_args_list}
        assert completed_ids == {"id-milk", "id-eggs"}

    def test_logs_sync_started(self):
        _configure_db()
        client, _ = _make_client()

        with patch("app.alexa.get_shopping_list_items", return_value=[]), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert any("Sync started" in e["message"] for e in db.get_recent_logs())

    def test_logs_sync_complete(self):
        _configure_db()
        client, lst = _make_client()

        with patch("app.alexa.get_shopping_list_items", return_value=[_alexa_item("Milk")]), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert any("Sync complete" in e["message"] for e in db.get_recent_logs())

    def test_logs_added_item_name(self):
        _configure_db()
        client, lst = _make_client()

        with patch("app.alexa.get_shopping_list_items", return_value=[_alexa_item("Butter")]), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert any("Butter" in e["message"] for e in db.get_recent_logs())


# ── Skip / restore items ──────────────────────────────────────────────────────

class TestSyncItemStates:
    def test_skips_item_already_on_anylist_unchecked(self):
        _configure_db()
        existing = _anylist_item("Milk", is_checked=False)
        client, lst = _make_client(items=[existing])

        with patch("app.alexa.get_shopping_list_items", return_value=[_alexa_item("Milk")]), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.add_item.assert_not_called()
        client.uncheck_item.assert_not_called()

    def test_logs_skipped_item(self):
        _configure_db()
        existing = _anylist_item("Milk", is_checked=False)
        client, _ = _make_client(items=[existing])

        with patch("app.alexa.get_shopping_list_items", return_value=[_alexa_item("Milk")]), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert any("Skipped" in e["message"] for e in db.get_recent_logs())

    def test_restores_checked_item(self):
        _configure_db()
        existing = _anylist_item("Milk", is_checked=True, item_id="li-milk")
        client, lst = _make_client(items=[existing])

        with patch("app.alexa.get_shopping_list_items", return_value=[_alexa_item("Milk")]), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.uncheck_item.assert_called_once_with(lst.id, "li-milk")
        client.add_item.assert_not_called()

    def test_logs_restored_item(self):
        _configure_db()
        existing = _anylist_item("Milk", is_checked=True)
        client, _ = _make_client(items=[existing])

        with patch("app.alexa.get_shopping_list_items", return_value=[_alexa_item("Milk")]), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert any("Restored" in e["message"] for e in db.get_recent_logs())

    def test_item_matching_is_case_insensitive(self):
        _configure_db()
        existing = _anylist_item("MILK", is_checked=False)
        client, _ = _make_client(items=[existing])

        with patch("app.alexa.get_shopping_list_items", return_value=[_alexa_item("milk")]), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.add_item.assert_not_called()

    def test_skips_alexa_items_with_empty_value(self):
        _configure_db()
        client, lst = _make_client()
        items = [{"id": "1", "value": "", "completed": False},
                 {"id": "2", "value": "   ", "completed": False}]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.add_item.assert_not_called()

    def test_skips_alexa_items_missing_value_key(self):
        _configure_db()
        client, lst = _make_client()
        items = [{"id": "1", "completed": False}]  # no "value" key

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.add_item.assert_not_called()


# ── Exception handling ────────────────────────────────────────────────────────

class TestSyncExceptionHandling:
    def test_returns_false_on_anylist_login_exception(self):
        _configure_db()
        items = [_alexa_item("Milk")]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.AnyListClient.login", side_effect=RuntimeError("network error")):
            assert app._run_sync() is False

    def test_logs_error_on_anylist_exception(self):
        _configure_db()
        items = [_alexa_item("Milk")]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.AnyListClient.login", side_effect=RuntimeError("boom")):
            app._run_sync()

        assert any(e["level"] == "ERROR" for e in db.get_recent_logs())

    def test_does_not_mark_alexa_complete_when_anylist_raises(self):
        _configure_db()
        items = [_alexa_item("Milk")]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed") as mock_complete, \
             patch("app.AnyListClient.login", side_effect=Exception("fail")):
            app._run_sync()

        mock_complete.assert_not_called()

    def test_handles_connection_error_gracefully(self):
        _configure_db()
        items = [_alexa_item("Milk")]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.AnyListClient.login", side_effect=ConnectionRefusedError()):
            assert app._run_sync() is False

    def test_handles_unexpected_exception_type(self):
        """Any Exception subclass must be caught and return False."""
        _configure_db()
        items = [_alexa_item("Milk")]

        class SomeObscureError(Exception):
            pass

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.AnyListClient.login", side_effect=SomeObscureError("weird")):
            result = app._run_sync()

        assert result is False


# ── Adversarial inputs ────────────────────────────────────────────────────────

class TestSyncAdversarialInputs:
    def test_item_value_with_unicode(self):
        _configure_db()
        client, lst = _make_client()
        items = [_alexa_item("🥛 Milk")]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.add_item.assert_called_once_with(lst.id, "🥛 Milk")

    def test_item_value_with_special_chars(self):
        _configure_db()
        client, lst = _make_client()
        items = [_alexa_item("Coffee & Tea (2 packs)")]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.add_item.assert_called_once_with(lst.id, "Coffee & Tea (2 packs)")

    def test_item_value_with_whitespace_is_stripped(self):
        _configure_db()
        client, lst = _make_client()
        items = [{"id": "1", "value": "  Milk  ", "completed": False}]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed"), \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        client.add_item.assert_called_once_with(lst.id, "Milk")

    def test_very_large_item_list(self):
        _configure_db()
        client, lst = _make_client()
        items = [_alexa_item(f"Item {i}", item_id=f"id-{i}") for i in range(200)]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed") as mock_complete, \
             patch("app.AnyListClient.login", return_value=client):
            result = app._run_sync()

        assert result is True
        assert client.add_item.call_count == 200
        assert mock_complete.call_count == 200

    def test_only_completed_and_active_items_mixed(self):
        """3 active + 2 completed → only 3 added to AnyList, all 3 marked complete."""
        _configure_db()
        client, lst = _make_client()
        items = [
            _alexa_item("Milk"),
            _alexa_item("Eggs"),
            _alexa_item("OldBread", completed=True),
            _alexa_item("Butter"),
            _alexa_item("OldCheese", completed=True),
        ]

        with patch("app.alexa.get_shopping_list_items", return_value=items), \
             patch("app.alexa.mark_item_completed") as mock_complete, \
             patch("app.AnyListClient.login", return_value=client):
            app._run_sync()

        assert client.add_item.call_count == 3
        assert mock_complete.call_count == 3
