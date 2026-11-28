"""Teil für Teil aus der Suche heraus (3.9.14).

Anlass, 11.10.2026: Sven fragte, wie man eine Figur aus Einzelteilen
zusammenbaut. Das ging nur über die Wunschliste – Figur suchen, merken,
zur Wunschliste wechseln, dort aufklappen. „können wir das nicht auch (das
Puzzle) auch in der Suche irgendwie unterbringen. Wenn dort irgendwelche
teile nur vorhanden sind, wandert es automatisch in die Wunschliste?"
Und von einem gefundenen Einzelteil soll man zu den Figuren kommen, die es
tragen.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

import core
import figurteile
import main

DATEI = "\n".join(json.dumps(z) for z in [
    {"farben": {"1": "White", "11": "Black", "28": "Nougat"}},
    {"teil": "30408pb01", "name": "Helmet SW Stormtrooper Pattern", "art": "kopfbedeckung"},
    {"teil": "3626c", "name": "Minifigure, Head", "art": "kopf"},
    {"teil": "973pb", "name": "Torso Stormtrooper", "art": "torso"},
    {"item_no": "sw0001", "teile": [["30408pb01", 1, 1], ["3626c", 28, 1],
                                    ["973pb", 1, 1]], "dg": []},
    {"item_no": "sw0002", "teile": [["30408pb01", 1, 1], ["3626c", 11, 1]], "dg": []},
    {"item_no": "sw0003", "teile": [["30408pb01", 11, 1]], "dg": []},
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
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "pz.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer,"
            " created_at) VALUES ('carla', 'x', 1, 0, ?)",
            (int(time.time()),)).lastrowid
    monkeypatch.setattr(figurteile.requests, "get",
                        lambda url, **kw: Antwort(200, DATEI, {"ETag": '"p1"'}))
    # Kein Preisabruf im Hintergrund – der ginge ins Netz.
    monkeypatch.setattr(main, "_maybe_fetch_prices_async", lambda *a, **kw: None)
    figurteile._figuren_merker.update({"stand": None, "index": {}})
    figurteile.ziehen()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "carla", True)
    return c


def _wuensche(client):
    return client.get("/api/wanted").json()["items"]


def test_der_erste_haken_setzt_die_figur_auf_die_wunschliste(client):
    """Der Anlass: In der Suche den Helm abhaken, und die Figur steht als
    „Im Bau 1/3" auf der Wunschliste – ohne vorher „Merken"."""
    assert _wuensche(client) == []
    r = client.put("/api/figurteile/sw0001/haken", json={
        "teile_da": ["30408pb01|1"], "name": "Stormtrooper",
        "img_url": "https://img.bricklink.com/ItemImage/MN/0/sw0001.png"}).json()
    assert (r["da"], r["gesamt"], r["neu"]) == (1, 3, True)
    w = _wuensche(client)
    assert len(w) == 1 and w[0]["item_id"] == "sw0001"
    assert w[0]["name"] == "Stormtrooper"
    assert w[0]["im_bau"] == {"da": 1, "gesamt": 3}


def test_die_suche_kennt_die_haken_von_der_wunschliste(client):
    """Was auf der Wunschliste abgehakt ist, steht in der Suche schon da –
    und der nächste Haken legt keinen zweiten Wunsch an."""
    client.put("/api/figurteile/sw0001/haken", json={"teile_da": ["30408pb01|1"]})
    d = client.get("/api/figurteile/sw0001").json()
    assert d["auf_wunschliste"] is True and d["teile_da"] == ["30408pb01|1"]
    r = client.put("/api/figurteile/sw0001/haken", json={
        "teile_da": ["30408pb01|1", "3626c|28"]}).json()
    assert r["neu"] is False and r["da"] == 2
    assert len(_wuensche(client)) == 1


def test_ohne_haken_kommt_nichts_auf_die_wunschliste(client):
    """Aufklappen und nichts abhaken ändert nichts. Und alle Haken wieder
    weg lässt den Wunsch stehen – er kann Notizen oder eine Einkaufsliste
    haben."""
    r = client.put("/api/figurteile/sw0002/haken", json={"teile_da": []}).json()
    assert r["neu"] is False and _wuensche(client) == []
    assert client.get("/api/figurteile/sw0002").json()["auf_wunschliste"] is False
    client.put("/api/figurteile/sw0002/haken", json={"teile_da": ["3626c|11"]})
    client.put("/api/figurteile/sw0002/haken", json={"teile_da": []})
    w = _wuensche(client)
    assert len(w) == 1 and w[0]["im_bau"] is None


def test_vom_teil_zu_den_figuren(client):
    """Der weiße Helm steckt in sw0001 und sw0002, der schwarze nur in
    sw0003. Ohne Farbe (aus einer Liste, die keine kennt) alle drei."""
    d = client.get("/api/figurteile/teil/30408pb01", params={"farbe": "1"}).json()
    assert [f["item_no"] for f in d["figuren"]] == ["sw0001", "sw0002"]
    assert d["figuren"][0]["img_url"]
    d = client.get("/api/figurteile/teil/30408pb01", params={"farbe": "11"}).json()
    assert [f["item_no"] for f in d["figuren"]] == ["sw0003"]
    d = client.get("/api/figurteile/teil/30408pb01").json()
    assert [f["item_no"] for f in d["figuren"]] == ["sw0001", "sw0002", "sw0003"]
    assert d["weitere"] == 0


def test_die_oberflaeche_bietet_das_puzzle_in_suche_und_steckbrief():
    """Die Python-Tests führen app.js nicht aus. Geprüft wird, dass Suche
    und Steckbrief den Baustein aufrufen und er über den neuen Weg hakt."""
    import pathlib
    js = (pathlib.Path(__file__).parent.parent / "frontend" / "app.js").read_text()
    teil = js[js.index("async function loadSuggestDetail("):]
    teil = teil[:teil.index("\n}\n")]
    assert "teilFuerTeilAnbieten(" in teil and "inFigurenAnbieten(" in teil
    sb = js[js.index("async function steckbriefOeffnen("):]
    sb = sb[:sb.index("\n}\n")]
    assert "teilFuerTeilAnbieten(" in sb and "inFigurenAnbieten(" in sb
    bau = js[js.index("async function teilFuerTeilAnbieten("):]
    assert "/haken`" in bau[:bau.index("\n}\n")]
