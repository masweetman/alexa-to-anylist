"""Flask web application — Alexa → AnyList sync dashboard."""

import asyncio
import atexit
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import nodriver as uc
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, g, jsonify, redirect, render_template, request, url_for, flash, session
from werkzeug.security import check_password_hash, generate_password_hash

import alexa
import db
from pyanylist import AnyListClient

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── App & DB init ─────────────────────────────────────────────────────────────

app = Flask(__name__)

_secret_key = os.environ.get("SECRET_KEY")
if not _secret_key:
    _secret_key = secrets.token_hex(32)
    logging.getLogger(__name__).warning(
        "SECRET_KEY environment variable not set — using a random key. "
        "Sessions will not survive restarts. Set SECRET_KEY in production."
    )
app.secret_key = _secret_key

db.init_db()

# ── Time zone ─────────────────────────────────────────────────────────────────

TIMEZONE_GROUPS: list[tuple[str, list[str]]] = [
    ("UTC", ["UTC"]),
    ("Americas", [
        "America/Anchorage",
        "America/Los_Angeles",
        "America/Denver",
        "America/Phoenix",
        "America/Chicago",
        "America/New_York",
        "America/Toronto",
        "America/Halifax",
        "America/St_Johns",
        "America/Sao_Paulo",
        "America/Argentina/Buenos_Aires",
        "America/Santiago",
        "America/Bogota",
        "America/Lima",
        "America/Mexico_City",
        "Pacific/Honolulu",
    ]),
    ("Europe", [
        "Europe/London",
        "Europe/Dublin",
        "Europe/Lisbon",
        "Europe/Paris",
        "Europe/Berlin",
        "Europe/Rome",
        "Europe/Madrid",
        "Europe/Amsterdam",
        "Europe/Stockholm",
        "Europe/Helsinki",
        "Europe/Warsaw",
        "Europe/Athens",
        "Europe/Bucharest",
        "Europe/Istanbul",
        "Europe/Moscow",
    ]),
    ("Africa", [
        "Africa/Cairo",
        "Africa/Lagos",
        "Africa/Nairobi",
        "Africa/Johannesburg",
    ]),
    ("Asia", [
        "Asia/Dubai",
        "Asia/Karachi",
        "Asia/Kolkata",
        "Asia/Colombo",
        "Asia/Dhaka",
        "Asia/Bangkok",
        "Asia/Jakarta",
        "Asia/Singapore",
        "Asia/Shanghai",
        "Asia/Hong_Kong",
        "Asia/Taipei",
        "Asia/Seoul",
        "Asia/Tokyo",
    ]),
    ("Pacific / Oceania", [
        "Australia/Perth",
        "Australia/Darwin",
        "Australia/Adelaide",
        "Australia/Brisbane",
        "Australia/Sydney",
        "Australia/Melbourne",
        "Pacific/Auckland",
        "Pacific/Guam",
    ]),
]


def _convert_to_local(utc_str: str | None, tz_name: str) -> str:
    """Convert a stored UTC timestamp string to the user's local time."""
    if not utc_str:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M UTC"):
        try:
            dt = datetime.strptime(utc_str, fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    else:
        return utc_str  # unrecognized format — return as-is
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("UTC")
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")


# ── Site password auth ────────────────────────────────────────────────────────

@app.before_request
def _load_timezone() -> None:
    g.user_timezone = db.get_setting("timezone") or "UTC"


@app.template_filter("localtime")
def localtime_filter(utc_str: str | None) -> str:
    return _convert_to_local(utc_str, getattr(g, "user_timezone", "UTC"))


@app.before_request
def require_login() -> None:
    if request.endpoint in ("login", "forgot_password", "logout", "static"):
        return
    site_hash = db.get_setting("site_password_hash")
    if site_hash and not session.get("authenticated"):
        return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    # Preserve the destination; reject external URLs
    next_url = (request.args.get("next") or request.form.get("next") or "").strip()
    if not next_url.startswith("/"):
        next_url = ""

    if request.method == "POST":
        password = request.form.get("password", "")
        site_hash = db.get_setting("site_password_hash")
        if site_hash and check_password_hash(site_hash, password):
            session["authenticated"] = True
            return redirect(next_url or url_for("index"))
        flash("Incorrect password.", "error")

    return render_template("login.html", next_url=next_url)


@app.post("/logout")
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        db.reset_credentials()
        session.clear()
        flash("Password and credentials have been reset. Please re-enter your settings.", "info")
        return redirect(url_for("login"))
    return render_template("forgot_password.html")


# ── Amazon browser-auth state ─────────────────────────────────────────────────
# Shared across requests; protected by _auth_lock.

_auth_lock = threading.Lock()
_auth_state: dict[str, Any] = {
    "status": "idle",   # idle | starting | waiting | extracting | done | error
    "browser": None,
    "cookies": None,
    "error": None,
}


def _cookie_obj_to_dict(c: Any) -> dict:
    """Convert a nodriver Cookie object to a plain serialisable dict."""
    return {
        k: v
        for k, v in {
            "name": getattr(c, "name", None),
            "value": getattr(c, "value", None),
            "domain": getattr(c, "domain", None),
            "path": getattr(c, "path", None),
            "expires": getattr(c, "expires", None),
            "secure": getattr(c, "secure", None),
            "httpOnly": getattr(c, "httpOnly", None),
        }.items()
        if v is not None
    }


def _run_browser_thread(loop: asyncio.AbstractEventLoop) -> None:
    """Background thread — opens Amazon in a browser and waits for the user."""

    async def _flow() -> None:
        try:
            browser = await uc.start()
            await browser.get("https://www.amazon.com/")

            with _auth_lock:
                _auth_state["browser"] = browser
                _auth_state["status"] = "waiting"

            # Spin until the Flask route changes status to "extracting" or "idle"
            while True:
                with _auth_lock:
                    status = _auth_state["status"]
                if status != "waiting":
                    break
                await asyncio.sleep(0.3)

            with _auth_lock:
                extracting = _auth_state["status"] == "extracting"

            if extracting:
                raw = await browser.cookies.get_all(requests_cookie_format=True)
                cookies = [_cookie_obj_to_dict(c) for c in raw]
                with _auth_lock:
                    _auth_state["cookies"] = cookies
                    _auth_state["status"] = "done"

        except Exception as exc:
            logger.exception("Browser auth error: %s", exc)
            with _auth_lock:
                _auth_state["error"] = str(exc)
                _auth_state["status"] = "error"
        finally:
            try:
                with _auth_lock:
                    browser_ref = _auth_state.get("browser")
                if browser_ref:
                    browser_ref.stop()
            except Exception:
                pass

    loop.run_until_complete(_flow())


# ── Sync logic ────────────────────────────────────────────────────────────────

def _run_sync() -> bool:
    """Full sync: Alexa incomplete items → AnyList → mark complete on Alexa."""
    db.add_log("INFO", "── Sync started ──")

    # 1. Fetch incomplete Alexa items
    all_items = alexa.get_shopping_list_items()
    if all_items is None:
        db.add_log("ERROR", "Could not fetch Alexa shopping list — check Amazon cookies")
        return False

    active = [i for i in all_items if not i.get("completed", False)]
    db.add_log("INFO", f"Alexa: {len(active)} active item(s) found")

    if not active:
        db.add_log("INFO", "Nothing to sync — done")
        return True

    # 2. Push to AnyList via pyanylist
    email = db.get_setting("anylist_email", "")
    password = db.get_setting("anylist_password", "")
    list_name = db.get_setting("anylist_list_name", "Shopping List")

    if not email or not password:
        db.add_log("ERROR", "AnyList credentials not configured — see Settings")
        return False

    try:
        client = AnyListClient.login(email, password)
        lst = client.get_list_by_name(list_name)
        if lst is None:
            available = [ll.name for ll in client.get_lists()]
            db.add_log(
                "ERROR",
                f'AnyList list "{list_name}" not found. '
                f"Available: {', '.join(available)}",
            )
            return False

        for alexa_item in active:
            name = alexa_item.get("value", "").strip()
            if not name:
                continue
            existing = next(
                (i for i in lst.items if i.name.lower() == name.lower()), None
            )
            if existing and not existing.is_checked:
                db.add_log("INFO", f'  Skipped "{name}" — already on AnyList')
                continue
            if existing and existing.is_checked:
                client.uncheck_item(lst.id, existing.id)
                db.add_log("INFO", f'  Restored "{name}" (was checked off)')
            else:
                client.add_item(lst.id, name)
                db.add_log("INFO", f'  Added "{name}"')

    except Exception as exc:
        db.add_log("ERROR", f"AnyList push failed: {exc}")
        return False

    # 3. Mark items complete on Alexa so they won't sync again
    for item in active:
        alexa.mark_item_completed(item)

    db.add_log("INFO", "── Sync complete ──")
    return True


# ── Scheduler ─────────────────────────────────────────────────────────────────

_scheduler = BackgroundScheduler(timezone="UTC")
_scheduler.start()


def _stop_scheduler() -> None:
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        pass


atexit.register(_stop_scheduler)


def _apply_schedule(interval_minutes: int) -> None:
    """Set or clear the automatic sync job. 0 or negative = disabled."""
    _scheduler.remove_all_jobs()
    if interval_minutes > 0:
        _scheduler.add_job(
            _run_sync,
            "interval",
            minutes=interval_minutes,
            id="sync",
        )


# Resume any schedule saved in the database
_apply_schedule(int(db.get_setting("sync_interval_minutes") or 0))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    all_items = alexa.get_shopping_list_items()
    no_cookies = db.get_setting("amazon_cookies") is None
    active = [i for i in (all_items or []) if not i.get("completed", False)]
    logs = db.get_recent_logs(50)
    return render_template(
        "index.html",
        items=active,
        logs=logs,
        no_cookies=no_cookies,
        alexa_error=all_items is None and not no_cookies,
    )


@app.get("/settings")
def settings():
    s = db.get_all_settings()
    return render_template("settings.html", s=s, timezone_groups=TIMEZONE_GROUPS)


@app.post("/settings")
def settings_save():
    text_fields = [
        "anylist_email",
        "anylist_list_name",
        "amazon_url",
    ]
    for field in text_fields:
        val = request.form.get(field, "").strip()
        if val:
            db.set_setting(field, val)

    # Password — only save if the user typed something
    pwd = request.form.get("anylist_password", "").strip()
    if pwd:
        db.set_setting("anylist_password", pwd)

    # Cookies JSON — validate before saving
    cookies_raw = request.form.get("amazon_cookies", "").strip()
    if cookies_raw:
        try:
            parsed = json.loads(cookies_raw)
            if not isinstance(parsed, list):
                raise ValueError("Cookies JSON must be an array")
            db.set_setting("amazon_cookies", json.dumps(parsed))
            db.set_setting(
                "cookies_updated_at",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            )
        except (json.JSONDecodeError, ValueError) as exc:
            flash(f"Invalid cookies JSON: {exc}", "error")
            return redirect(url_for("settings"))

    # Site password
    site_pwd = request.form.get("site_password", "").strip()
    if site_pwd:
        db.set_setting("site_password_hash", generate_password_hash(site_pwd))
        session["authenticated"] = True
    elif request.form.get("site_password_clear"):
        db.set_setting("site_password_hash", "")
        session.pop("authenticated", None)

    # Time zone
    tz_val = request.form.get("timezone", "").strip()
    if tz_val:
        db.set_setting("timezone", tz_val)

    # Sync interval
    interval_raw = request.form.get("sync_interval_minutes", "").strip()
    if interval_raw != "":
        try:
            interval = int(interval_raw)  # raises ValueError for floats and non-numeric
            if interval < 0:
                raise ValueError("interval must be 0 or greater")
            db.set_setting("sync_interval_minutes", str(interval))
            _apply_schedule(interval)
        except ValueError:
            flash("Sync interval must be a whole number of minutes (0 to disable).", "error")
            return redirect(url_for("settings"))

    flash("Settings saved.", "success")
    return redirect(url_for("settings"))


@app.post("/sync")
def sync_now():
    """Start sync in a background thread and redirect to dashboard."""
    t = threading.Thread(target=_run_sync, daemon=True)
    t.start()
    flash("Sync started — refresh in a moment to see results.", "info")
    return redirect(url_for("index"))


# ── Amazon browser auth ───────────────────────────────────────────────────────

@app.post("/auth/start")
def auth_start():
    with _auth_lock:
        status = _auth_state["status"]

    if status in ("waiting", "extracting", "starting"):
        return jsonify({"status": "already_running"})

    with _auth_lock:
        _auth_state.update(
            {"status": "starting", "browser": None, "cookies": None, "error": None}
        )

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=_run_browser_thread, args=(loop,), daemon=True)
    t.start()
    return jsonify({"status": "started"})


@app.get("/auth/status")
def auth_status():
    with _auth_lock:
        return jsonify(
            {"status": _auth_state["status"], "error": _auth_state.get("error")}
        )


@app.post("/auth/complete")
def auth_complete():
    with _auth_lock:
        if _auth_state["status"] != "waiting":
            return (
                jsonify({"ok": False, "error": "No auth session waiting for completion"}),
                400,
            )
        _auth_state["status"] = "extracting"

    # Wait up to 12 seconds for cookie extraction to finish
    for _ in range(24):
        with _auth_lock:
            status = _auth_state["status"]
        if status in ("done", "error"):
            break
        time.sleep(0.5)

    with _auth_lock:
        status = _auth_state["status"]
        cookies = _auth_state.get("cookies")
        error = _auth_state.get("error")

    if status != "done" or not cookies:
        return jsonify({"ok": False, "error": error or "Cookie extraction timed out"}), 500

    cookies_json = json.dumps(cookies, indent=2)
    db.set_setting("amazon_cookies", cookies_json)
    db.set_setting(
        "cookies_updated_at",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    with _auth_lock:
        _auth_state["status"] = "idle"

    return jsonify({"ok": True, "count": len(cookies)})


# ── API helpers (JSON) ────────────────────────────────────────────────────────

@app.get("/api/logs")
def api_logs():
    return jsonify(db.get_recent_logs(100))


@app.post("/api/logs/clear")
def api_logs_clear():
    db.clear_logs()
    return jsonify({"ok": True})


@app.get("/api/schedule/status")
def schedule_status():
    job = _scheduler.get_job("sync")
    interval = int(db.get_setting("sync_interval_minutes") or 0)
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return jsonify({"enabled": job is not None, "interval_minutes": interval, "next_run": next_run})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(db.get_setting("server_port") or 5123)
    app.run(debug=False, host="0.0.0.0", port=port)
