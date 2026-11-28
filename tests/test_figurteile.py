"""Teile je Figur, Doppelgänger und „Im Bau" (3.9.0).

Anlass, 09.10.2026: Kann Nupplo sagen, dass sw1422 und sw1423 sich nur im
Entfernungsmesser am Helm unterscheiden? Und kann man eine Figur Teil für
Teil sammeln – Kopf und Torso da, Beine fehlen, „Im Bau 2/3" in der
Wunschliste? Die Teile kommen aus der Datei des Katalogdienstes.
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
    {"farben": {"1": "White", "86": "Light Bluish Gray", "28": "Nougat"},
     "farben_de": {"1": "Weiß", "86": "Hellblaugrau"}},
    {"teil": "2019pb07", "name": "Helmet Clone Trooper Phase 2", "art": "kopfbedeckung"},
    {"teil": "4499", "name": "Headwear Accessory Rangefinder", "art": "kopfbedeckung",
     "de": "Entfernungsmesser"},
    {"teil": "3626cpb3262", "name": "Minifig Head Clone Trooper", "art": "kopf"},
    {"teil": "973pb5123c01", "name": "Torso Clone Commander Armor", "art": "torso"},
    {"teil": "970c00pb0042", "name": "Hips and White Legs", "art": "beine"},
    {"item_no": "sw1422", "rb": "fig-016588",
     "teile": [["2019pb07", 1, 1], ["3626cpb3262", 28, 1],
               ["973pb5123c01", 1, 1], ["970c00pb0042", 1, 1]],
     "dg": [{"item_no": "sw1423", "hier": [], "dort": ["4499|86"],
             "verdeckt": False}]},
    {"item_no": "sw1423", "rb": "fig-016587",
     "teile": [["2019pb07", 1, 1], ["4499", 86, 1], ["3626cpb3262", 28, 1],
               ["973pb5123c01", 1, 1], ["970c00pb0042", 1, 1]],
     "dg": [{"item_no": "sw1422", "hier": ["4499|86"], "dort": [],
             "verdeckt": False}]},
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
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "ft.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer,"
            " created_at) VALUES ('carla', 'x', 1, 0, ?)",
            (int(time.time()),)).lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "carla", True)
    return c


@pytest.fixture
def datei(monkeypatch):
    abrufe = []

    def holen(url, headers=None, **kw):
        abrufe.append(dict(headers or {}))
        if headers and headers.get("If-None-Match") == '"v1"':
            return Antwort(304)
        return Antwort(200, DATEI, {"ETag": '"v1"'})

    monkeypatch.setattr(figurteile.requests, "get", holen)
    return abrufe


def test_der_steckbrief_nennt_den_entfernungsmesser(client, datei):
    """sw1423 sieht aus wie sw1422 – der Unterschied ist der
    Entfernungsmesser, mit Name und Farbe, nicht nur als Nummer."""
    assert figurteile.ziehen()["geholt"] == 2
    d = client.get("/api/figurteile/sw1423").json()
    assert d["quelle"] == "katalog"
    p = d["doppelgaenger"][0]
    assert p["item_no"] == "sw1422"
    assert [(t["name"], t["color_name"]) for t in p["hier"]] == [
        ("Headwear Accessory Rangefinder", "Light Bluish Gray")]
    assert p["dort"] == []
    arten = [t["art_name"] for t in d["teile"]]
    assert arten == ["Kopfbedeckung", "Kopfbedeckung", "Kopf", "Oberkörper", "Beine"]


def test_teile_gehen_ohne_bricklink(client, datei, monkeypatch):
    """Bis 3.8 gab es die Teile einer Figur nur mit BrickLink-Schlüssel.
    Mit der Datei braucht „Enthaltene Teile anzeigen" keinen mehr."""
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    figurteile.ziehen()
    items = client.get("/api/fig_parts/sw1422").json()["items"]
    assert [p["item_id"] for p in items] == ["2019pb07", "3626cpb3262",
                                            "973pb5123c01", "970c00pb0042"]
    assert items[0]["color_name"] == "White"
    assert items[0]["img_url"].endswith("/PN/1/2019pb07.png")


def test_im_bau_zaehlt_die_abgehakten_teile(client, datei):
    """Kopf und Torso da, der Rest fehlt: „Im Bau 2/4" an der Karte, und
    ohne Häkchen ist die Figur wieder ein gewöhnlicher Wunsch."""
    figurteile.ziehen()
    client.post("/api/wanted", json={"item_id": "sw1422", "item_type": "minifig",
                                     "name": "Clone Trooper"})
    w = client.get("/api/wanted").json()["items"][0]
    assert w["im_bau"] is None and w["teile_da"] == []
    r = client.put(f"/api/wanted/{w['id']}/teile",
                   json={"teile_da": ["3626cpb3262|28", "973pb5123c01|1"]}).json()
    assert (r["da"], r["gesamt"]) == (2, 4)
    w = client.get("/api/wanted").json()["items"][0]
    assert w["im_bau"] == {"da": 2, "gesamt": 4}
    client.put(f"/api/wanted/{w['id']}/teile", json={"teile_da": []})
    assert client.get("/api/wanted").json()["items"][0]["im_bau"] is None


def test_die_haekchen_gehen_mit_dem_abgleich(client, datei):
    """Die iOS-App führt dieselbe Wunschliste. Die Häkchen müssen im
    Abgleich stehen, sonst sieht sie die Figur nicht als „im Bau" – und
    eine Änderung muss den Stand hochzählen, sonst holt sie sie nie."""
    figurteile.ziehen()
    client.post("/api/wanted", json={"item_id": "sw1422", "item_type": "minifig",
                                     "name": "Clone Trooper"})
    wid = client.get("/api/wanted").json()["items"][0]["id"]
    vorher = client.get("/api/sync/pull?since=0").json()["rev"]
    client.put(f"/api/wanted/{wid}/teile", json={"teile_da": ["973pb5123c01|1"]})
    nachher = client.get(f"/api/sync/pull?since={vorher}").json()
    saetze = [c for c in nachher["changes"] if c["table"] == "wanted"]
    assert len(saetze) == 1
    assert json.loads(saetze[0]["fields"]["teile_da"]) == ["973pb5123c01|1"]


def test_unveraenderte_datei_wird_nicht_neu_eingelesen(client, datei):
    figurteile.ziehen()
    assert figurteile.ziehen() == {"geholt": 0, "grund": "unverändert"}
    assert datei[1]["If-None-Match"] == '"v1"'
    assert client.get("/api/figurteile/sw1423").json()["quelle"] == "katalog"


def test_ohne_datei_bleibt_bricklink(client, monkeypatch):
    """Solange die Datei nicht veröffentlicht ist (404), antwortet BrickLink
    wie bisher – ohne Doppelgänger, aber mit Häkchen-Schlüsseln."""
    monkeypatch.setattr(figurteile.requests, "get", lambda *a, **k: Antwort(404))
    assert figurteile.ziehen()["grund"] == "keine Datei"
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    monkeypatch.setattr(integrations, "bricklink_minifig_parts", lambda no: [
        {"item_id": "973pb0001", "item_type": "part", "name": "Torso",
         "color_id": 11, "color_name": "Black", "qty": 1, "img_url": "",
         "bricklink_url": ""}])
    d = client.get("/api/figurteile/sw0001").json()
    assert d["quelle"] == "bricklink"
    assert d["teile"][0]["key"] == "973pb0001|11"
    assert d["doppelgaenger"] == []


def test_der_scanhinweis_fragt_bricklink_nicht(client, monkeypatch):
    """Anlass: Unter den Scan-Treffern steht seit 3.9.0 die Prüffrage zu
    einem Doppelgänger. Dafür fragt die Seite bis zu drei Kandidaten auf
    einmal – ohne Datei hätte jeder einen BrickLink-Abruf gekostet, und
    Doppelgänger kennt BrickLink ohnehin nicht."""
    monkeypatch.setattr(figurteile.requests, "get", lambda *a, **k: Antwort(404))
    figurteile.ziehen()
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    gefragt = []
    monkeypatch.setattr(integrations, "bricklink_minifig_parts",
                        lambda no: gefragt.append(no) or [])
    d = client.get("/api/figurteile/sw0001?nur_datei=1").json()
    assert d["doppelgaenger"] == [] and gefragt == []


def test_die_teile_gibt_es_auch_auf_deutsch(client, datei):
    """Anlass, 10.10.2026: Die Prüffrage unter dem Scan-Treffer stand
    englisch da („Utensil Rangefinder (Black)"). Die Datei bringt das Ding
    auf Deutsch und die deutsche Farbe mit; wo eine fehlt, bleibt sie leer
    und die Oberfläche nimmt die englische."""
    figurteile.ziehen()
    p = client.get("/api/figurteile/sw1423").json()["doppelgaenger"][0]
    t = p["hier"][0]
    assert (t["name_de"], t["color_name_de"]) == ("Entfernungsmesser", "Hellblaugrau")
    kopf = [x for x in client.get("/api/figurteile/sw1423").json()["teile"]
            if x["item_id"] == "3626cpb3262"][0]
    assert (kopf["name_de"], kopf["color_name_de"], kopf["color_name"]) == ("", "", "Nougat")


def test_testinstanzen_bekommen_die_spalte_nachgeruestet(client, datei):
    """Die Testinstanz hatte die Tabelle schon ohne `name_de` – ohne
    Nachrüsten scheiterte das Einlesen der neuen Datei."""
    with core.db() as conn:
        conn.execute("DROP TABLE figurteile_teil")
        conn.execute("CREATE TABLE figurteile_teil (nr TEXT PRIMARY KEY,"
                     " name TEXT NOT NULL DEFAULT '', art TEXT NOT NULL DEFAULT '')")
    assert figurteile.ziehen()["geholt"] == 2


def test_der_hinweis_steht_auch_beim_manuellen_erfassen():
    """Anlass, 10.10.2026: Sven suchte sw1422 unter „Manuell erfassen" und
    bekam keinen Hinweis auf sw1423 – den gab es nur unter den
    Scan-Treffern. Beide Trefferlisten müssen ihn zeichnen."""
    import pathlib
    js = (pathlib.Path(__file__).parent.parent / "frontend" / "app.js").read_text()
    for liste in ("function renderSuggestions(", "function renderScanResults("):
        rumpf = js[js.index(liste):]
        rumpf = rumpf[:rumpf.index("\nfunction ", 1)]
        assert "scanDoppelgaenger(box, items)" in rumpf, liste


def test_sets_der_figur_kommen_zuerst_aus_der_datei(client, monkeypatch):
    """Anlass 13.10.2026: sw1549–sw1554 (75457) zeigten kein Set, weil
    BrickLink sie noch keinem zugeordnet hatte. Die Teiledatei nennt die
    Sets aus Rebrickable; BrickLink wird erst gefragt, wenn sie nichts weiß –
    im Wirkbetrieb wollen wir von der API weg (Sven)."""
    zeilen = [json.loads(z) for z in DATEI.splitlines()]
    zeilen[-1]["sets"] = ["75457-1"]
    text = "\n".join(json.dumps(z) for z in zeilen) + "\n"
    monkeypatch.setattr(figurteile.requests, "get",
                        lambda *a, **k: Antwort(200, text, {"ETag": '"s1"'}))
    figurteile.ziehen()
    with core.db() as conn:
        conn.execute("INSERT INTO set_daten (nr, name) VALUES ('75457-1',"
                     " 'Executor Super Star Destroyer')")
        conn.execute("INSERT INTO set_namen (nr, sprache, name) VALUES"
                     " ('75457-1', 'de', 'Supersternzerstörer Executor')")
    gefragt = []
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    monkeypatch.setattr(integrations, "bricklink_supersets",
                        lambda nr: gefragt.append(nr) or [{"no": "x-1", "name": "BL", "qty": 1}])
    s = client.get("/api/fig_sets/SW1423").json()["sets"]
    assert s == [{"no": "75457-1", "name": "Supersternzerstörer Executor",
                  "qty": 1, "quelle": "katalog"}]
    assert gefragt == []
    # Weiß die Datei nichts, bleibt BrickLink der Rückfall.
    assert client.get("/api/fig_sets/sw1422").json()["sets"][0]["no"] == "x-1"
    assert gefragt == ["sw1422"]
