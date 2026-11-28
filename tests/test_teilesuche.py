"""Einzelteile im manuellen Erfassen finden – auf Deutsch, mit Farbe.

Anlass (10.10.2026, Svens Bildschirmfoto): „weißer stormtrooper helm mit
gelb aufdruck" fand mit Typ „Einzelteil" nichts. Teile kamen nur von
Rebrickable, und dort sind sie englisch und farblos. Die Teile der Figuren
liegen seit 3.9.0 mit BrickLink-Nummer, Farbe und beschreibendem Namen in
der Instanz – darin wird jetzt zuerst gesucht.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import core
import figurteile
import integrations
import main

DATEI = "\n".join(json.dumps(z) for z in [
    {"farben": {"1": "White", "3": "Yellow", "11": "Black", "86": "Light Bluish Gray"}},
    {"teil": "30408pb01", "name": "Minifigure, Headgear Helmet SW Stormtrooper Pattern",
     "art": "kopfbedeckung"},
    {"teil": "30408pb99", "name": "Minifigure, Headgear Helmet SW Stormtrooper "
     "with Yellow Stripes Pattern", "art": "kopfbedeckung"},
    {"teil": "30408", "name": "Minifigure, Headgear Helmet SW Stormtrooper",
     "art": "kopfbedeckung"},
    {"teil": "30408pb50", "name": "Minifigure, Headgear Helmet SW Stormtrooper "
     "with White Visor Pattern", "art": "kopfbedeckung"},
    {"teil": "3626c", "name": "Minifigure, Head", "art": "kopf"},
    {"teil": "x1", "name": "Minifigure, Headgear Helmet Construction", "art": "kopfbedeckung"},
    {"item_no": "sw0001", "teile": [["30408pb01", 1, 1], ["3626c", 3, 1]], "dg": []},
    {"item_no": "sw0002", "teile": [["30408pb99", 1, 1], ["3626c", 11, 1]], "dg": []},
    {"item_no": "sw0003", "teile": [["30408pb99", 11, 1], ["30408", 1, 1]], "dg": []},
    {"item_no": "cty0001", "teile": [["x1", 3, 1]], "dg": []},
    # Ein schwarzer Helm mit weißem Visier, häufiger verbaut als alle weißen
    {"item_no": "sw0004", "teile": [["30408pb50", 11, 1]], "dg": []},
    {"item_no": "sw0005", "teile": [["30408pb50", 11, 1]], "dg": []},
]) + "\n"


class Antwort:
    def __init__(self, code, text="", headers=None):
        self.status_code, self.text = code, text
        self.content = text.encode()
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "ts.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer,"
            " created_at) VALUES ('carla', 'x', 1, 0, ?)",
            (int(time.time()),)).lastrowid
    monkeypatch.setattr(integrations, "rebrickable_enabled", lambda: False)
    monkeypatch.setattr(figurteile.requests, "get",
                        lambda url, **kw: Antwort(200, DATEI, {"ETag": '"t1"'}))
    figurteile._teile_merker.update({"stand": None, "teile": []})
    figurteile.ziehen()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "carla", True)
    return c


def _suche(client, q):
    r = client.get("/api/search", params={"q": q, "item_type": "part"})
    assert r.status_code == 200, r.text
    return [(i["item_id"], i["color_id"]) for i in r.json()["items"]]


def test_svens_helm(client):
    """Der Anlass wörtlich: weiß ist das Teil, gelb der Aufdruck."""
    assert _suche(client, "weißer stormtrooper helm mit gelb aufdruck")[0] \
        == ("30408pb99", 1)


def test_farbe_des_teils_kommt_zuerst(client):
    """„weißer Stormtrooper-Helm": Der schwarze Helm mit weißem Visier ist
    öfter verbaut und gehört auch in die Liste – aber hinter die weißen,
    denn die zuerst genannte Farbe ist meist die des Teils."""
    treffer = _suche(client, "weißer stormtrooper helm")
    assert ("30408pb50", 11) in treffer
    assert treffer[-1] == ("30408pb50", 11)
    assert treffer[0][1] == 1


def test_aufdruck_verlangt_einen_aufdruck(client):
    """Der glatte Helm (`30408`, ohne „Pattern" und ohne `pb`) ist kein Helm
    mit Aufdruck."""
    treffer = _suche(client, "stormtrooper helm mit aufdruck")
    assert ("30408", 1) not in treffer
    assert ("30408pb01", 1) in treffer
    assert ("30408", 1) in _suche(client, "stormtrooper helm")


def test_name_und_farbe_in_der_liste(client):
    r = client.get("/api/search", params={"q": "weißer stormtrooper helm",
                                          "item_type": "part"}).json()
    erster = r["items"][0]
    assert erster["name"].endswith("(White)")
    assert erster["sub"] == "White"
    assert erster["img_url"].startswith("https://img.bricklink.com/ItemImage/PN/1/")
    assert r["eigene_leer"] is False


def test_farbe_allein_ist_keine_teilesuche(client):
    """„weiß" allein träfe Tausende Teile – das ist kein Suchbegriff."""
    assert figurteile.suchen("weiß") == []


def test_andere_typen_bleiben_unberuehrt(client):
    r = client.get("/api/search", params={"q": "stormtrooper helm",
                                          "item_type": "set"})
    assert r.json().get("items", []) == []
