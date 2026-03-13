"""Shared pytest fixtures."""
import pytest
import db
import app as flask_app


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect every test to its own fresh SQLite database."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()


@pytest.fixture(autouse=True)
def reset_auth_state():
    """Reset the global browser-auth state before each test."""
    with flask_app._auth_lock:
        flask_app._auth_state.update(
            {"status": "idle", "browser": None, "cookies": None, "error": None}
        )


@pytest.fixture(autouse=True)
def clear_scheduler_jobs():
    """Ensure no scheduled jobs leak between tests."""
    flask_app._scheduler.remove_all_jobs()
    yield
    flask_app._scheduler.remove_all_jobs()


@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c
