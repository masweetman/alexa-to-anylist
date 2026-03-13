"""Alexa shopping-list API wrapper.

Reads Amazon session cookies from the database instead of a file.
"""

import json
import logging
from typing import Any

import requests

import db

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 13_5_1 like Mac OS X)"
        " AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        " PitanguiBridge/2.2.345247.0-[HARDWARE=iPhone10_4][SOFTWARE=13.5.1]"
    ),
    "Accept": "*/*",
    "Accept-Language": "*",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
}


def _amazon_url() -> str:
    return db.get_setting("amazon_url", "https://www.amazon.com").rstrip("/")  # type: ignore[union-attr]


def _build_session() -> requests.Session | None:
    """Return a requests.Session with Amazon cookies loaded from the database."""
    cookies_json = db.get_setting("amazon_cookies")
    if not cookies_json:
        logger.error("No Amazon cookies in database — add them on the Settings page.")
        return None
    try:
        cookies_list: list[dict] = json.loads(cookies_json)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse Amazon cookies JSON: %s", exc)
        return None

    session = requests.Session()
    session.headers.update(_DEFAULT_HEADERS)
    for c in cookies_list:
        name = c.get("name")
        value = c.get("value")
        if name and value:
            session.cookies.set(
                name=name,
                value=value,
                domain=c.get("domain"),
                path=c.get("path"),
            )
    return session


def get_shopping_list_items() -> list[dict[str, Any]] | None:
    """Return all items (complete + incomplete) from the Alexa shopping list."""
    session = _build_session()
    if not session:
        return None
    url = f"{_amazon_url()}/alexashoppinglists/api/getlistitems"
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for val in data.values():
            if isinstance(val, dict) and "listItems" in val:
                return val["listItems"]
        logger.warning("Unexpected Alexa API response — could not find listItems key")
        return []
    except requests.RequestException as exc:
        logger.error("Failed to fetch Alexa shopping list: %s", exc)
        return None


def mark_item_completed(list_item: dict[str, Any]) -> bool:
    """Mark a single shopping list item as completed on Alexa."""
    session = _build_session()
    if not session:
        return False
    url = f"{_amazon_url()}/alexashoppinglists/api/updatelistitem"
    payload = {**list_item, "completed": True}
    try:
        resp = session.put(url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info("Marked complete on Alexa: %s", list_item.get("value", ""))
        return True
    except requests.RequestException as exc:
        logger.error("Failed to mark item complete on Alexa: %s", exc)
        return False
