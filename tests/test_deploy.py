"""One-service deploy: the dashboard is served at "/" without changing how any API route answers,
and DEPLOY_PUBLIC switches on safer defaults."""
import os
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.config import default_prompt_preview_chars
from app.main import mount_dashboard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_with_dashboard(tmp_path) -> FastAPI:
    (tmp_path / "index.html").write_text("<h1>dashboard</h1>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    app = FastAPI()
    app.post("/chat")(lambda: {"answer": "hi"})
    app.api_route("/health", methods=["GET", "HEAD"])(lambda: {"status": "ok"})
    app.get("/stats")(lambda: {"requests": 0})
    assert mount_dashboard(app, str(tmp_path)) is True
    return app


def test_dashboard_and_assets_are_served(tmp_path):
    client = TestClient(app_with_dashboard(tmp_path))
    assert client.get("/").text == "<h1>dashboard</h1>"
    assert client.head("/").status_code == 200
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/docs").status_code == 200


def test_api_answers_exactly_as_without_the_dashboard(tmp_path):
    client = TestClient(app_with_dashboard(tmp_path), follow_redirects=False)
    assert client.get("/chat").status_code == 405  # wrong method, not "not found"
    assert client.head("/health").status_code == 200
    assert client.get("/stats/").status_code == 307  # trailing-slash redirect still works
    assert client.get("/no-such-page").status_code == 404


def test_real_health_route_accepts_head():
    assert TestClient(main.app).head("/health").status_code == 200


def test_no_build_folder_means_no_dashboard(tmp_path):
    assert mount_dashboard(FastAPI(), str(tmp_path / "missing")) is False
    assert mount_dashboard(FastAPI(), "") is False


def test_public_deploy_stores_no_prompt_text_by_default():
    assert default_prompt_preview_chars(public=True) == "0"
    assert default_prompt_preview_chars(public=False) == "60"


def import_config(env: dict, expr: str) -> subprocess.CompletedProcess:
    """Import app.config in a fresh process (it reads the environment once, at import). Every
    variable a test cares about is set explicitly: load_dotenv() never overrides a variable that is
    already set, so a local .env can't change the result."""
    return subprocess.run(
        [sys.executable, "-c", f"import app.config as c; print({expr})"],
        env={**os.environ, **env}, capture_output=True, text=True, cwd=REPO,
    )


def test_preview_length_can_be_set_and_zero_is_allowed():
    public = {"DEPLOY_PUBLIC": "true", "GATEWAY_API_KEYS": "k1", "PROMPT_PREVIEW_CHARS": "20"}
    off = {"DEPLOY_PUBLIC": "false", "PROMPT_PREVIEW_CHARS": "0"}
    assert import_config(public, "c.PROMPT_PREVIEW_CHARS").stdout.strip() == "20"
    assert import_config(off, "c.PROMPT_PREVIEW_CHARS").stdout.strip() == "0"


def test_public_deploy_without_client_keys_refuses_to_start():
    result = import_config({"DEPLOY_PUBLIC": "true", "GATEWAY_API_KEYS": ""}, "1")
    assert result.returncode != 0
    assert "DEPLOY_PUBLIC=true needs GATEWAY_API_KEYS" in result.stderr
