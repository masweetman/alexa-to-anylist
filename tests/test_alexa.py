"""Unit tests for alexa.py — Alexa shopping list API wrapper."""
import json
from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

import alexa
import db

VALID_COOKIES = [
    {"name": "session-id", "value": "abc123", "domain": ".amazon.com", "path": "/"},
    {"name": "ubid-main", "value": "xyz456", "domain": ".amazon.com", "path": "/"},
]
VALID_COOKIES_JSON = json.dumps(VALID_COOKIES)

ALEXA_RESPONSE = {
    "shoppingList": {
        "listItems": [
            {"id": "1", "value": "Milk", "completed": False},
            {"id": "2", "value": "Eggs", "completed": True},
        ]
    }
}


def _settings(cookies=VALID_COOKIES_JSON, url="https://www.amazon.com"):
    """Return a get_setting side-effect that returns cookies and url."""
    def _get(key, default=None):
        if key == "amazon_cookies":
            return cookies
        if key == "amazon_url":
            return url
        return default
    return _get


def _ok_response(body: dict) -> MagicMock:
    r = MagicMock()
    r.json.return_value = body
    r.raise_for_status.return_value = None
    return r


def _error_response(status=401) -> MagicMock:
    r = MagicMock()
    r.raise_for_status.side_effect = req_lib.HTTPError(f"{status} Error")
    return r


# ── _build_session ────────────────────────────────────────────────────────────

class TestBuildSession:
    def test_returns_none_when_no_cookies_in_db(self):
        with patch("alexa.db.get_setting", _settings(cookies=None)):
            assert alexa._build_session() is None

    def test_returns_none_when_cookies_is_empty_string(self):
        with patch("alexa.db.get_setting", _settings(cookies="")):
            assert alexa._build_session() is None

    def test_returns_none_on_invalid_json(self):
        with patch("alexa.db.get_setting", _settings(cookies="not-valid-json")):
            assert alexa._build_session() is None

    def test_returns_none_on_json_object_not_array(self):
        # Malformed: JSON object instead of array
        with patch("alexa.db.get_setting", _settings(cookies='{"name":"x"}')):
            session = alexa._build_session()
        assert session is None

    def test_returns_session_with_valid_cookies(self):
        with patch("alexa.db.get_setting", _settings()):
            assert alexa._build_session() is not None

    def test_session_has_correct_cookie_values(self):
        with patch("alexa.db.get_setting", _settings()):
            session = alexa._build_session()
        assert session.cookies["session-id"] == "abc123"
        assert session.cookies["ubid-main"] == "xyz456"

    def test_skips_cookie_entry_missing_name(self):
        bad = [{"value": "abc", "domain": ".amazon.com"}]
        with patch("alexa.db.get_setting", _settings(cookies=json.dumps(bad))):
            session = alexa._build_session()
        assert session is not None
        assert len(list(session.cookies.keys())) == 0

    def test_skips_cookie_entry_missing_value(self):
        bad = [{"name": "session-id", "domain": ".amazon.com"}]
        with patch("alexa.db.get_setting", _settings(cookies=json.dumps(bad))):
            session = alexa._build_session()
        assert session is not None
        assert len(list(session.cookies.keys())) == 0

    def test_empty_cookie_list_returns_session(self):
        with patch("alexa.db.get_setting", _settings(cookies="[]")):
            assert alexa._build_session() is not None

    def test_user_agent_header_is_set(self):
        with patch("alexa.db.get_setting", _settings()):
            session = alexa._build_session()
        assert "User-Agent" in session.headers
        assert "iPhone" in session.headers["User-Agent"]

    def test_accepts_headers_are_set(self):
        with patch("alexa.db.get_setting", _settings()):
            session = alexa._build_session()
        assert session.headers.get("Accept") == "*/*"

    def test_partial_cookie_fields_ok(self):
        # Cookie with only name and value — no domain/path
        minimal = [{"name": "foo", "value": "bar"}]
        with patch("alexa.db.get_setting", _settings(cookies=json.dumps(minimal))):
            session = alexa._build_session()
        assert session.cookies["foo"] == "bar"


# ── get_shopping_list_items ───────────────────────────────────────────────────

class TestGetShoppingListItems:
    def test_returns_none_when_no_cookies(self):
        with patch("alexa.db.get_setting", _settings(cookies=None)):
            assert alexa.get_shopping_list_items() is None

    def test_returns_list_items(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", return_value=_ok_response(ALEXA_RESPONSE)):
            result = alexa.get_shopping_list_items()
        assert len(result) == 2
        assert result[0]["value"] == "Milk"

    def test_returns_empty_list_when_no_listItems_key(self):
        body = {"someList": {"otherKey": []}}
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", return_value=_ok_response(body)):
            result = alexa.get_shopping_list_items()
        assert result == []

    def test_returns_none_on_http_error(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", return_value=_error_response(401)):
            assert alexa.get_shopping_list_items() is None

    def test_returns_none_on_connection_error(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", side_effect=req_lib.ConnectionError("down")):
            assert alexa.get_shopping_list_items() is None

    def test_returns_none_on_timeout(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", side_effect=req_lib.Timeout("timed out")):
            assert alexa.get_shopping_list_items() is None

    def test_calls_correct_endpoint(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", return_value=_ok_response(ALEXA_RESPONSE)) as mock_get:
            alexa.get_shopping_list_items()
        url = mock_get.call_args[0][0]
        assert "alexashoppinglists/api/getlistitems" in url

    def test_uses_amazon_url_from_settings(self):
        with patch("alexa.db.get_setting", _settings(url="https://www.amazon.co.uk")), \
             patch("requests.Session.get", return_value=_ok_response(ALEXA_RESPONSE)) as mock_get:
            alexa.get_shopping_list_items()
        assert "amazon.co.uk" in mock_get.call_args[0][0]

    def test_trailing_slash_stripped_from_amazon_url(self):
        with patch("alexa.db.get_setting", _settings(url="https://www.amazon.com/")), \
             patch("requests.Session.get", return_value=_ok_response(ALEXA_RESPONSE)) as mock_get:
            alexa.get_shopping_list_items()
        url = mock_get.call_args[0][0]
        assert "//" not in url.replace("https://", "")

    def test_passes_timeout_to_get(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", return_value=_ok_response(ALEXA_RESPONSE)) as mock_get:
            alexa.get_shopping_list_items()
        assert mock_get.call_args[1].get("timeout") is not None

    def test_handles_empty_response_body(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.get", return_value=_ok_response({})):
            result = alexa.get_shopping_list_items()
        assert result == []


# ── mark_item_completed ───────────────────────────────────────────────────────

class TestMarkItemCompleted:
    def test_returns_false_when_no_session(self):
        with patch("alexa.db.get_setting", _settings(cookies=None)):
            assert alexa.mark_item_completed({"id": "1", "value": "Milk"}) is False

    def test_returns_true_on_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp):
            assert alexa.mark_item_completed({"id": "1", "value": "Milk"}) is True

    def test_sends_completed_true_in_payload(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp) as mock_put:
            alexa.mark_item_completed({"id": "1", "value": "Milk", "completed": False})
        payload = mock_put.call_args[1]["json"]
        assert payload["completed"] is True

    def test_preserves_all_original_item_fields(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        item = {"id": "42", "value": "Eggs", "completed": False, "type": "TASK"}
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp) as mock_put:
            alexa.mark_item_completed(item)
        payload = mock_put.call_args[1]["json"]
        assert payload["id"] == "42"
        assert payload["value"] == "Eggs"
        assert payload["type"] == "TASK"

    def test_does_not_mutate_original_item(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        item = {"id": "1", "value": "Milk", "completed": False}
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp):
            alexa.mark_item_completed(item)
        assert item["completed"] is False  # original unchanged

    def test_returns_false_on_http_error(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=_error_response(500)):
            assert alexa.mark_item_completed({"id": "1", "value": "Milk"}) is False

    def test_returns_false_on_connection_error(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", side_effect=req_lib.ConnectionError()):
            assert alexa.mark_item_completed({"id": "1", "value": "Milk"}) is False

    def test_returns_false_on_timeout(self):
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", side_effect=req_lib.Timeout()):
            assert alexa.mark_item_completed({"id": "1", "value": "Milk"}) is False

    def test_calls_correct_endpoint(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp) as mock_put:
            alexa.mark_item_completed({"id": "1", "value": "Milk"})
        url = mock_put.call_args[0][0]
        assert "alexashoppinglists/api/updatelistitem" in url

    def test_passes_timeout_to_put(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp) as mock_put:
            alexa.mark_item_completed({"id": "1", "value": "Milk"})
        assert mock_put.call_args[1].get("timeout") is not None

    def test_empty_item_dict_does_not_crash(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp):
            result = alexa.mark_item_completed({})
        assert result is True

    def test_already_completed_item_is_sent_as_completed(self):
        """Marking an already-completed item should still set completed=True."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("alexa.db.get_setting", _settings()), \
             patch("requests.Session.put", return_value=mock_resp) as mock_put:
            alexa.mark_item_completed({"id": "1", "value": "Milk", "completed": True})
        assert mock_put.call_args[1]["json"]["completed"] is True
