"""Der optionale Verweis auf LEGOs Bauanleitungen (08.10.2026 gewünscht).

Wichtig: ausgeschaltet voreingestellt, wie jeder Weg nach draußen, und
wählbar, wo er erscheint – nur in der Browseransicht, nur in der
Handyansicht oder in beiden.
"""
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core
import main

APP_JS = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "ba.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at)"
            " VALUES ('anna', 'x', 1, ?)", (int(time.time()),)).lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    return c


def test_ausgeschaltet_voreingestellt(client):
    assert client.get("/api/config").json()["bauanleitung"] == "aus"


@pytest.mark.parametrize("wo", ["browser", "handy", "beide", "aus"])
def test_die_wahl_haelt(client, wo):
    assert client.post("/api/settings/bauanleitung", json={"wo": wo}).status_code == 200
    assert client.get("/api/config").json()["bauanleitung"] == wo


def test_unbekannte_wahl_wird_abgewiesen(client):
    assert client.post("/api/settings/bauanleitung", json={"wo": "immer"}).status_code == 422


def test_adresse_nimmt_die_setnummer_ohne_variante():
    """BrickLink schreibt `75192-1`, LEGO kennt nur `75192`."""
    fn = re.search(r"function bauanleitungUrl\(setNr\) \{.*?\n\}\n", APP_JS, re.S).group(0)
    assert 'replace(/-\\d+$/, "")' in fn
    assert "/service/building-instructions/" in fn
    # Figurennummern wie sw1479 sind keine Setnummern.
    assert "^\\d{3,7}$" in fn


def test_sichtbar_nur_nach_wahl():
    fn = re.search(r"function bauanleitungSichtbar\(\) \{.*?\n\}\n", APP_JS, re.S).group(0)
    assert '"aus"' in fn and '"beide"' in fn and "matchMedia" in fn
