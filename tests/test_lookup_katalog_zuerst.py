"""/api/lookup mit eigenem BrickLink-Schlüssel: zuerst der Katalog.

Vorfall 17.10.2026: Sven sah in der Handerfassung zu sw0518 „BrickLink kennt
diese Nummer nicht“, obwohl der Nupplo-Katalog die Figur kennt. Die
Nummernprüfung fragte BrickLink live statt der eingestellten Quelle. iPhone
und Mac fragen seitdem zuerst den Katalog. Die SE fragte mit Schlüssel bis
3.9.21 immer zuerst BrickLink und verbrauchte dabei das Tageskontingent
für Nummern, die der Katalog längst kannte.
"""
import time

import pytest

import core
import integrations
import main
from fastapi.testclient import TestClient


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "zuerst.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer, "
            "created_at) VALUES ('tester', 'x', 0, 0, ?)",
            (int(time.time()),)).lastrowid
        conn.execute(
            "INSERT INTO katalog_index (item_no, item_type, name, such, woerter,"
            " img_url, farben, art, updated_at) VALUES ('sw0518', 'minifig',"
            " 'Clone Scout Trooper', '', ' clone scout trooper ',"
            " 'https://img.bricklink.com/ItemImage/MN/0/sw0518.png', '', '', 1)")
        conn.execute(
            "INSERT INTO katalog_index (item_no, item_type, name, such, woerter,"
            " img_url, farben, art, updated_at) VALUES ('sw0001', 'minifig',"
            " 'Battle Droid', '', ' battle droid ', '', '', '', 1)")
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    gefragt = []

    def bl(item_type, item_no):
        gefragt.append(item_no)
        return {"item_id": item_no, "item_type": item_type, "name": "von BL",
                "img_url": f"https://img.bricklink.com/neu/{item_no}.png"}
    monkeypatch.setattr(integrations, "bricklink_item", bl)
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "tester", False)
    return c, gefragt


def test_bekannte_nummer_fragt_bricklink_nicht(ctx):
    c, gefragt = ctx
    d = c.get("/api/lookup/minifig/sw0518").json()
    assert d["quelle"] == "katalog" and d["name"] == "Clone Scout Trooper"
    assert gefragt == [], "der Katalog kannte die Nummer"


def test_unbekannte_nummer_geht_an_bricklink(ctx):
    c, gefragt = ctx
    d = c.get("/api/lookup/minifig/sw9999").json()
    assert d["name"] == "von BL" and gefragt == ["sw9999"]


def test_katalogtreffer_ohne_bild_holt_es_bei_bricklink(ctx):
    c, gefragt = ctx
    d = c.get("/api/lookup/minifig/sw0001").json()
    assert d["img_url"].endswith("neu/sw0001.png") and gefragt == ["sw0001"]


def test_bild_neu_laden_fragt_bricklink(ctx):
    """Das ↻ am Bild will ausdrücklich das Bild von BrickLink."""
    c, gefragt = ctx
    d = c.get("/api/lookup/minifig/sw0518?frisch=1").json()
    assert d["img_url"].endswith("neu/sw0518.png") and gefragt == ["sw0518"]
