"""/api/set_figs ohne eigenen BrickLink-Zugang: der gespeicherte Inhalt
zählt, ein unbekanntes Set ist kein Fehler mehr (vorher 501)."""
import time

import pytest
import requests

import core
import integrations
import main
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "setfigs.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer, "
            "created_at) VALUES ('tester', 'x', 0, 0, ?)",
            (int(time.time()),)).lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "tester", False)
    return c


def _inhalt(set_no, figs):
    with core.db() as conn:
        for no, qty, name in figs:
            conn.execute("INSERT INTO set_contents (set_no, fig_no, qty, name) "
                         "VALUES (?, ?, ?, ?)", (set_no, no, qty, name))
        conn.execute("INSERT INTO set_meta (set_no, figs_fetched_at) VALUES (?, ?)",
                     (set_no, int(time.time())))


def test_ohne_bl_aus_gespeichertem_inhalt(client, monkeypatch):
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _inhalt("10188-1", [("sw0011a", 1, "Chewbacca"), ("sw0019", 2, None)])
    r = client.get("/api/set_figs/10188-1")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["quelle"] == "lokal"
    assert [(f["item_id"], f["qty"]) for f in d["items"]] == [("sw0011a", 1), ("sw0019", 2)]
    assert d["items"][1]["name"] == "sw0019"
    assert d["items"][0]["img_url"].endswith("/sw0011a.png")


def test_ohne_variante_findet_minus_eins(client, monkeypatch):
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _inhalt("75318-1", [("sw1113", 1, "Grogu")])
    assert client.get("/api/set_figs/75318").json()["items"][0]["item_id"] == "sw1113"


def test_ohne_bl_leer_gespeichert_gilt_als_unbekannt(client, monkeypatch):
    # 75455-1 wurde im August leer gespeichert, BrickLink führt sw1538 erst
    # später – ein leerer Inhalt darf nicht als „keine Figuren“ gelten.
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _inhalt("75455-1", [])
    d = client.get("/api/set_figs/75455-1").json()
    assert d == {"items": [], "quelle": "", "unbekannt": True}


def test_ohne_bl_unbekanntes_set_kein_501(client, monkeypatch):
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    r = client.get("/api/set_figs/99999-1")
    assert r.status_code == 200
    assert r.json() == {"items": [], "quelle": "", "unbekannt": True}


def test_bl_gestoert_faellt_auf_gespeichertes_zurueck(client, monkeypatch):
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)

    def kaputt(_):
        raise requests.ConnectionError("weg")
    monkeypatch.setattr(integrations, "bricklink_subsets", kaputt)
    _inhalt("10188-1", [("sw0011a", 1, "Chewbacca")])
    d = client.get("/api/set_figs/10188-1").json()
    assert d["quelle"] == "lokal" and d["items"][0]["item_id"] == "sw0011a"


def test_bl_gestoert_ohne_gespeichertes_bleibt_502(client, monkeypatch):
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)

    def kaputt(_):
        raise requests.ConnectionError("weg")
    monkeypatch.setattr(integrations, "bricklink_subsets", kaputt)
    assert client.get("/api/set_figs/99999-1").status_code == 502
