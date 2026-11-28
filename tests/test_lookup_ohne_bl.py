"""/api/lookup ohne eigenen BrickLink-Zugang: Antwort aus dem eigenen
Katalog statt 501, eine unbekannte Nummer ist 404."""
import time

import pytest

import core
import integrations
import main
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "lookup.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer, "
            "created_at) VALUES ('tester', 'x', 0, 0, ?)",
            (int(time.time()),)).lastrowid
        conn.execute(
            "INSERT INTO katalog_index (item_no, item_type, name, such, woerter,"
            " img_url, farben, art, updated_at) VALUES ('sw0041', 'minifig',"
            " 'Emperor Palpatine', '', ' emperor palpatine ',"
            " 'https://img.bricklink.com/ItemImage/MN/0/sw0041.png', '', '', 1)")
        conn.execute(
            "INSERT INTO katalog_index (item_no, item_type, name, such, woerter,"
            " img_url, farben, art, category_id, jahr, updated_at) VALUES ('7190-1', 'set',"
            " 'Millennium Falcon', '', ' millennium falcon ', '', '', '',"
            " 'rb:Star Wars', '2000', 1)")
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)

    def nie(*a, **k):
        raise AssertionError("ohne Zugang darf BrickLink nicht gefragt werden")
    monkeypatch.setattr(integrations, "bricklink_item", nie)
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "tester", False)
    return c


def test_figur_aus_dem_katalog(client):
    r = client.get("/api/lookup/minifig/sw0041")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["quelle"] == "katalog" and d["name"] == "Emperor Palpatine"
    assert d["img_url"].endswith("sw0041.png")
    assert d["bricklink_url"].endswith("M=sw0041")


def test_set_auch_ohne_variante(client):
    d = client.get("/api/lookup/set/7190").json()
    assert d["item_id"] == "7190-1" and d["year"] == 2000
    assert d["category_id"] is None, "rb:-Thema ist keine BrickLink-Kategorie"


def test_falscher_typ_ist_unbekannt(client):
    assert client.get("/api/lookup/set/sw0041").status_code == 404


def test_unbekannte_nummer_404_statt_501(client):
    r = client.get("/api/lookup/minifig/sw9999")
    assert r.status_code == 404
    assert r.json()["detail"] == "Diese Nummer ist im Katalog nicht bekannt."
