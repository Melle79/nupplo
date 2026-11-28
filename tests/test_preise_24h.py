"""Figurenpreise aus dem Hub nie älter als 26 Stunden, Setpreise bleiben (3.9.30).

Nach fehlenden Setpreisen sollte ein später bereitgestellter Setpreis
nicht wieder aus der Sammlung verschwinden, nur weil der nächste Abruf noch
nicht stattgefunden hat. Alte Figurenpreise werden weiter geleert; alte
Setpreise bleiben mit ihrem letzten Preisstand als Schätzwert erhalten.
"""
import gzip
import json
import time

import pytest
import requests
from fastapi.testclient import TestClient

import core
import hubpreise
import integrations
import main

JETZT = int(time.time())


class Antwort:
    def __init__(self, status, content=b"", data=None, headers=None):
        self.status_code = status
        self.content = content
        self._data = data
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def _abzug(*saetze):
    zeilen = [json.dumps({"kurse": {}, "stand": "x", "gebiet": "DE",
                          "anzahl": len(saetze)})]
    zeilen += [json.dumps(s) for s in saetze]
    return gzip.compress(("\n".join(zeilen) + "\n").encode())


def _satz(nr, alter_s, t="minifig", z="U", avg=3.5):
    return {"t": t, "nr": nr, "z": z, "art": "sold", "gebiet": "DE",
            "genutzt": "DE", "min": 1.0, "avg": avg, "max": 9.0,
            "anzahl": 5, "geholt_am": JETZT - alter_s}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "p24.db"))
    core.init_db()
    core.set_setting("price_region", "DE")
    integrations._PRICE_CACHE.clear()
    main._set_anfragen.clear()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer,"
            " created_at) VALUES ('anna', 'x', 1, 1, ?)", (JETZT,)).lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    return c


@pytest.fixture
def hub(monkeypatch):
    log = {"anfragen": [], "abzug": _abzug(), "antwort": {"status": "vorgemerkt"}}

    def post(url, json=None, **kw):
        if url.endswith("/v1/preise/anfrage"):
            log["anfragen"].append(json)
            return Antwort(200, data=log["antwort"])
        return Antwort(201, data={"token": "prz_test"})

    def get(url, params=None, headers=None, **kw):
        if (params or {}).get("gebiet", "DE") != "DE":
            return Antwort(200, content=_abzug())
        return Antwort(200, content=log["abzug"], headers={"etag": '"e"'})

    monkeypatch.setattr(hubpreise.requests, "post", post)
    monkeypatch.setattr(hubpreise.requests, "get", get)
    return log


def _eintrag(item_id, alter_s=None, t="minifig", preis=5.0):
    with core.db() as conn:
        return conn.execute(
            "INSERT INTO collection (item_id, item_type, name, quantity,"
            " condition, price_new, price_used, price_updated_at, price_data,"
            " added_at) VALUES (?, ?, 'x', 1, 'used', ?, ?, ?, ?, ?)",
            (item_id, t, preis, preis,
             None if alter_s is None else JETZT - alter_s,
             json.dumps({"used": {"avg": str(preis)}}), JETZT)).lastrowid


def test_eine_konstante():
    assert hubpreise.FRISCH_STUNDEN == 26
    assert hubpreise.FRISCH_S == 26 * 3600


def test_alter_figurenpreis_bleibt_mit_stand(client):
    """Seit 3.9.31 verschwindet kein Preis mehr (30.10.2026). Anlass:
    In einer Testsammlung standen an dem Tag 173 statt 161 Einträge ohne Preis,
    weil Figurenpreise nach 26 Stunden gelöscht wurden."""
    alt = _eintrag("sw0001", alter_s=27 * 3600)
    set_alt = _eintrag("75192-1", alter_s=27 * 3600, t="set", preis=10.0)
    neu = _eintrag("sw0002", alter_s=3600)
    assert main._preise_verfallen() == 0
    with core.db() as conn:
        r = conn.execute("SELECT * FROM collection WHERE id = ?", (alt,)).fetchone()
        assert r["price_used"] == 5.0 and r["price_updated_at"]
        r = conn.execute("SELECT * FROM collection WHERE id = ?",
                         (set_alt,)).fetchone()
        assert r["price_new"] == 10.0 and r["price_used"] == 10.0
        assert r["price_data"] and r["price_updated_at"]
        assert conn.execute("SELECT price_used FROM collection WHERE id = ?",
                            (neu,)).fetchone()[0] == 5.0
    stats = client.get("/api/stats/dashboard").json()
    assert stats["totals"]["value"] == 20.0


def test_alter_preis_bleibt_in_der_sammlung(client):
    _eintrag("sw0001", alter_s=27 * 3600)
    main._verfall_zuletzt[0] = 0
    items = client.get("/api/collection").json()["items"]
    assert items[0]["price_used"] == 5.0


def test_abzug_satz_aelter_als_48h_kommt_mit_stand(client, hub):
    hub["abzug"] = _abzug(_satz("sw0001", 3600), _satz("sw0002", 27 * 3600),
                          _satz("sw0003", 50 * 3600))
    hubpreise.laden("DE", erzwingen=True)
    p1 = integrations.price_guide("minifig", "sw0001", "U")
    assert p1["avg"] == "3.5000" and p1["stand"] is None
    assert integrations.price_guide("minifig", "sw0002", "U")["stand"] is None
    p3 = integrations.price_guide("minifig", "sw0003", "U")
    assert p3["avg"] == "3.5000" and p3["stand"]


def test_hub_preis_behaelt_seinen_stand(client, hub):
    """Ein Preis vom Vortag lebt hier nicht noch einmal 24 Stunden."""
    hub["abzug"] = _abzug(_satz("sw0001", 20 * 3600), _satz("sw0001", 20 * 3600, z="N"))
    hubpreise.laden("DE", erzwingen=True)
    eid = _eintrag("sw0001")
    main._preise_auffrischen()
    with core.db() as conn:
        stand = conn.execute("SELECT price_updated_at FROM collection WHERE"
                             " id = ?", (eid,)).fetchone()[0]
    assert stand == JETZT - 20 * 3600


def test_alter_setpreis_bleibt_bis_zur_erneuerung_im_sammlungswert(client, hub):
    """Der Hub kennzeichnet Setpreise über 26 Stunden als alt; ihr letzter
    Betrag soll die Sammlung trotzdem bis zum nächsten Abruf bewerten."""
    alt_n = _satz("75192-1", 3 * 86400, t="set", z="N", avg=800.0)
    alt_u = _satz("75192-1", 3 * 86400, t="set", z="U", avg=600.0)
    alt_n["alt"] = alt_u["alt"] = True
    hub["abzug"] = _abzug(alt_n, alt_u)
    hubpreise.laden("DE", erzwingen=True)
    eid = _eintrag("75192-1", alter_s=3 * 86400, t="set", preis=2.0)

    p = client.get(f"/api/collection/{eid}/price").json()

    assert p["used"]["avg"] == "600.0000"
    assert p["updated_at"] == alt_u["geholt_am"]
    with core.db() as conn:
        row = conn.execute("SELECT price_used, price_updated_at FROM collection "
                           "WHERE id = ?", (eid,)).fetchone()
        history = conn.execute(
            "SELECT ts, price_used FROM price_history WHERE item_id = ?",
            ("75192-1",)).fetchall()
    assert row["price_used"] == 600.0 and row["price_updated_at"] == alt_u["geholt_am"]
    assert len(history) == 1
    assert history[0]["ts"] == alt_u["geholt_am"]
    assert history[0]["price_used"] == 600.0
    assert client.get("/api/stats/dashboard").json()["totals"]["value"] == 600.0
    client.get(f"/api/collection/{eid}/price?refresh=1")
    with core.db() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM price_history WHERE item_id = ?",
            ("75192-1",)).fetchone()[0] == 1


def test_alter_eintrag_wird_beim_oeffnen_neu_geholt(client, hub):
    hub["abzug"] = _abzug(_satz("sw0001", 3600, avg=7.0))
    hubpreise.laden("DE", erzwingen=True)
    eid = _eintrag("sw0001", alter_s=30 * 3600, preis=2.0)
    p = client.get(f"/api/collection/{eid}/price").json()
    assert p["used"]["avg"] == "7.0000"


def test_set_ohne_preis_wird_angefragt(client, hub):
    r = client.get("/api/price/set/75192")
    assert r.status_code == 404
    assert "angefragt" in r.json()["detail"]
    assert hub["anfragen"] == [{"t": "set", "nr": "75192-1", "gebiet": "DE"}]


def test_set_wird_nur_einmal_je_stunde_angefragt(client, hub):
    """Jedes Öffnen eines Sets ohne Preis ging erneut an den Hub, obwohl er
    eben „vorgemerkt“ gesagt hatte; die iOS-App wartet eine Stunde
    (Durchsicht 17.10.2026). Der Satz darunter bleibt derselbe."""
    for _ in range(3):
        r = client.get("/api/price/set/75192")
        assert "angefragt" in r.json()["detail"]
    assert len(hub["anfragen"]) == 1
    (schluessel,) = main._set_anfragen
    main._set_anfragen[schluessel] = ("vorgemerkt", time.time() - 3700)
    client.get("/api/price/set/75192")
    assert len(hub["anfragen"]) == 2


def test_set_frisch_vom_hub(client, hub):
    hub["antwort"] = {"status": "frisch", "preise": [
        _satz("75192-1", 600, t="set", z="N", avg=800.0),
        _satz("75192-1", 600, t="set", z="U", avg=600.0)]}
    p = client.get("/api/price/set/75192").json()
    assert p["new"]["avg"] == "800.0000" and p["used"]["avg"] == "600.0000"


def test_set_nicht_im_dienst(client, hub):
    hub["antwort"] = {"status": "nicht_im_dienst"}
    r = client.get("/api/price/set/10305")
    assert r.status_code == 404 and "keinen Preis" in r.json()["detail"]


def test_figuren_werden_nie_einzeln_angefragt(client, hub):
    assert client.get("/api/price/minifig/njo0001").status_code == 404
    assert hub["anfragen"] == []


def test_verlauf_neben_jedem_preis(client):
    with core.db() as conn:
        conn.execute("INSERT INTO price_history (item_id, item_type, ts,"
                     " price_new, price_used, source) VALUES"
                     " ('sw0001', 'minifig', ?, 4, 3, 'auto')", (JETZT - 9 * 86400,))
    _eintrag("sw0001", alter_s=30 * 3600)
    main._preise_verfallen()
    d = client.get("/api/history/minifig/sw0001").json()
    assert d["frisch"] is True and len(d["points"]) == 1   # alter Preis bleibt
    with core.db() as conn:
        conn.execute("UPDATE collection SET price_used = 5, price_updated_at = ?",
                     (JETZT - 60,))
    assert len(client.get("/api/history/minifig/sw0001").json()["points"]) == 1


def _schluessel():
    for k in integrations.BL_KEYS:
        core.set_setting(k, "x")


def test_eigener_schluessel_holt_nach_gewaehlten_stunden(client, monkeypatch):
    """Vorgabe 24 Stunden; wer 72 einstellt, holt einen 30 Stunden alten
    Preis noch nicht neu – und er bleibt sichtbar."""
    _schluessel()
    assert main.preisabruf_stunden() == 24
    eid = _eintrag("sw0001", alter_s=30 * 3600)
    gerufen = []
    monkeypatch.setattr(main, "_fetch_and_store_prices",
                        lambda *a, **k: gerufen.append(a))
    client.post("/api/settings/preisabruf", json={"stunden": 72})
    main._preise_verfallen()
    main._preise_auffrischen()
    assert gerufen == []
    with core.db() as conn:
        assert conn.execute("SELECT price_used FROM collection WHERE id = ?",
                            (eid,)).fetchone()[0] == 5.0
    r = client.post("/api/settings/preisabruf", json={"stunden": 24}).json()
    assert r["abruf_stunden"] == 24
    main._preise_auffrischen()
    assert len(gerufen) == 1
    assert client.post("/api/settings/preisabruf",
                       json={"stunden": 721}).status_code == 422


def test_mit_schluessel_nie_der_hub(client, hub, monkeypatch):
    """Entweder – oder: Wer einen Schlüssel hat, nimmt keine zentralen
    Preise, und seine eigenen gehen nicht zum Hub."""
    _schluessel()
    gerufen = []
    monkeypatch.setattr(integrations, "_price_request",
                        lambda *a, **k: gerufen.append(a) or {})
    client.get("/api/price/set/75192")
    assert gerufen and hub["anfragen"] == []
    quelle = (main.__file__)
    import pathlib
    for datei in ("hub.py", "community.py", "main.py"):
        text = (pathlib.Path(quelle).parent / datei).read_text()
        assert "PUT\", hub.HUB_URL, \"/v1/preise" not in text
        assert '"/v1/preise",' not in text


def test_protokoll_zeigt_auch_aeltere(client):
    with core.db() as conn:
        for ts in (JETZT - 27 * 3600, JETZT - 3600):
            conn.execute("INSERT INTO price_history (item_id, item_type, ts,"
                         " price_new, price_used, source) VALUES"
                         " ('sw0001', 'minifig', ?, 4, 3, 'auto')", (ts,))
    e = client.get("/api/price_log").json()["entries"]
    assert [x["ts"] for x in e] == [JETZT - 3600, JETZT - 27 * 3600]


def test_angebote_nur_mit_eigenem_schluessel(client, hub):
    """Der Preisdienst führt nur Verkäufe. Ohne Schlüssel keine „ab“-Zeile
    und kein Schalter, der nichts täte (15.10.2026)."""
    hub["abzug"] = _abzug(_satz("sw0001", 3600),
                          dict(_satz("sw0001", 3600), art="stock"))
    hubpreise.laden("DE", erzwingen=True)
    client.post("/api/settings/angebotspreise", json={"an": True})
    assert client.get("/api/config").json()["angebotspreise"] is False
    p = client.get("/api/price/minifig/sw0001?angebote=1").json()
    assert p["stock"] == {}
    r = client.post("/api/prices/angebote",
                    json={"items": [{"item_no": "sw0001"}]}).json()
    assert r == {"angebote": {}}
    _schluessel()
    assert client.get("/api/config").json()["angebotspreise"] is True
