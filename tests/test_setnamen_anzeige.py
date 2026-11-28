"""Deutsche Setnamen in Sammlung, Wunschliste und Listen – nur zur
Anzeige, der gespeicherte Name bleibt (10.10.2026)."""
import time
from pathlib import Path

import pytest

import core
import main
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "sna.db"))
    core.init_db()
    t = int(time.time())
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer, "
            "created_at) VALUES ('tester', 'x', 1, 1, ?)", (t,)).lastrowid
        for no, typ, name in (("75457-1", "set", "Executor Super Star Destroyer"),
                              ("75335-1", "set", "BD-1"),
                              ("sw0041", "minifig", "Emperor Palpatine")):
            conn.execute("INSERT INTO collection (item_id, item_type, name, quantity,"
                         " condition, added_at) VALUES (?, ?, ?, 1, 'used', ?)",
                         (no, typ, name, t))
        conn.execute("INSERT INTO wanted (item_id, item_type, name, added_at)"
                     " VALUES ('75457-1', 'set', 'Executor Super Star Destroyer', ?)", (t,))
        conn.execute("INSERT INTO set_namen (nr, sprache, name, woerter) VALUES"
                     " ('75457-1', 'de', 'Supersternzerstörer Executor', '')")
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "tester", True)
    return c


def _nach_id(items):
    return {i["item_id"]: i for i in items}


def test_sammlung_bekommt_name_de_und_behaelt_name(client):
    items = _nach_id(client.get("/api/collection").json()["items"])
    assert items["75457-1"]["name_de"] == "Supersternzerstörer Executor"
    assert items["75457-1"]["name"] == "Executor Super Star Destroyer"
    assert "name_de" not in items["75335-1"], "ohne deutschen Namen bleibt es englisch"
    assert "name_de" not in items["sw0041"], "nur Sets"


def test_suche_findet_beide_namen(client):
    for q in ("Supersternzerstörer", "Super Star"):
        ids = [i["item_id"] for i in client.get("/api/collection", params={"q": q}).json()["items"]]
        assert ids == ["75457-1"], q


def test_wunschliste_bekommt_name_de(client):
    items = client.get("/api/wanted").json()["items"]
    assert items[0]["name_de"] == "Supersternzerstörer Executor"


def test_gespeicherter_name_bleibt(client):
    client.get("/api/collection")
    with core.db() as conn:
        assert conn.execute("SELECT name FROM collection WHERE item_id = '75457-1'"
                            ).fetchone()[0] == "Executor Super Star Destroyer"


def test_oberflaeche_nutzt_anzeigenamen():
    app = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "function nameAnzeige(it)" in app
    assert app.count("esc(nameAnzeige(it))") >= 3   # Sammlung, Wunschliste, Listen
