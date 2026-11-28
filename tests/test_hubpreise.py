"""Preisquelle Hub (Labor 3.9.0): Tagesabzug holen, lokal nachschlagen.

Wichtig: Im Hub-Modus geht keine einzelne Nummer hinaus, und ohne eigenen
BrickLink-Schlüssel gibt es trotzdem Preise.
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


@pytest.fixture(autouse=True)
def echter_zugang(monkeypatch):
    """Hier wird das Anlegen des Preis-Zugangs selbst geprüft – gegen einen
    nachgebauten Hub, den die Tests unten stellen."""
    monkeypatch.setattr(hubpreise, "_token", hubpreise._echter_token)


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


def _abzug(*saetze, kurse=None):
    zeilen = [json.dumps({"kurse": kurse or {"USD": 1.1}, "stand": "2026-10-08",
                          "gebiet": "DE", "anzahl": len(saetze)})]
    zeilen += [json.dumps(s) for s in saetze]
    return gzip.compress(("\n".join(zeilen) + "\n").encode())


def _teilabzug(saetze, teil, bis):
    zeilen = [json.dumps({"kurse": {"USD": 1.1}, "stand": "2026-10-15",
                          "gebiet": "DE", "anzahl": len(saetze), "bis": bis,
                          "teil": teil, "frisch_stunden": 26})]
    zeilen += [json.dumps(x) for x in saetze]
    return gzip.compress(("\n".join(zeilen) + "\n").encode())


SATZ = {"t": "minifig", "nr": "sw0001a", "z": "U", "art": "sold",
        "gebiet": "DE", "genutzt": "europe", "min": 2.0, "avg": 3.5,
        "max": 5.0, "anzahl": 12, "geholt_am": int(time.time()) - 3600}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "hp.db"))
    core.init_db()
    integrations._PRICE_CACHE.clear()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at)"
            " VALUES ('anna', 'x', 1, ?)", (int(time.time()),)).lastrowid
        uid2 = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at)"
            " VALUES ('ben', 'x', 0, ?)", (int(time.time()),)).lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    c.ben = "Bearer " + core.create_token(uid2, "ben", False)
    return c


@pytest.fixture
def hub(monkeypatch):
    """Ein falscher Hub: zählt, wer was fragt."""
    log = {"post": [], "get": [], "abzug": _abzug(SATZ), "andere": [],
           "andere_abzug": {}}

    def post(url, **kw):
        log["post"].append(url)
        return Antwort(201, data={"token": "prz_test"})

    def get(url, params=None, headers=None, **kw):
        g = (params or {}).get("gebiet", "DE")
        if g != "DE":
            # Rückfall-Gebiete der Kette (seit 3.9.x): eigene Abzüge, hier leer
            log["andere"].append(g)
            return Antwort(200, content=log["andere_abzug"].get(g, _abzug()),
                           headers={"etag": '"x-%s"' % g})
        log["get"].append((url, params, dict(headers or {})))
        if "seit" in (params or {}):
            # Nachladen (Hub-PR #12): nur das Neue, oder 304
            if log.get("teil") is None:
                return Antwort(304)
            return Antwort(200, content=log["teil"])
        if headers and headers.get("if-none-match") == '"e1"':
            return Antwort(304)
        return Antwort(200, content=log["abzug"], headers={"etag": '"e1"'})

    monkeypatch.setattr(hubpreise.requests, "post", post)
    monkeypatch.setattr(hubpreise.requests, "get", get)
    return log


def _schluessel_eintragen():
    for k in integrations.BL_KEYS:
        core.set_setting(k, "x")


def test_ohne_schluessel_der_hub_mit_schluessel_bricklink(client):
    """Entweder – oder (3.9.17): Der eigene Schlüssel entscheidet."""
    s = client.get("/api/settings/preisquelle").json()
    assert s["quelle"] == "hub" and s["an"] is True
    _schluessel_eintragen()
    s = client.get("/api/settings/preisquelle").json()
    assert s["quelle"] == "bricklink" and s["an"] is False


def test_umschalten_holt_den_abzug_und_preise_gehen_ohne_schluessel(client, hub):
    core.set_setting("price_region", "DE")
    assert not integrations.bricklink_enabled()
    r = client.post("/api/settings/preisquelle/laden").json()
    assert r["quelle"] == "hub" and r["saetze"] == 1
    assert r["geladen"] == {"neu": 1, "teil": False}
    assert client.get("/api/config").json()["bricklink_prices"] is True
    p = client.get("/api/price/minifig/sw0001a").json()
    assert p["used"]["avg"] == "3.5000" and p["used"]["used_scope"] == "europe"
    assert p["used"]["fell_back"] is True and p["used"]["times_sold"] == 12
    # Abzug als Ganzes – keine Nummer im Aufruf
    url, params, _ = hub["get"][0]
    assert url.endswith("/v1/preise/abzug") and params == {"gebiet": "DE", "mit_alt": "1"}


def test_fehlender_preis_ist_404_nicht_null(client, hub):
    core.set_setting("price_region", "DE")
    client.post("/api/settings/preisquelle/laden")
    r = client.get("/api/price/minifig/sw9999")
    assert r.status_code == 404
    assert "Kein aktueller Preis" in r.json()["detail"]


def test_waehrung_wird_mit_dem_kurs_des_abzugs_umgerechnet(client, hub):
    core.set_setting("price_region", "DE")
    core.set_setting("currency", "USD")
    client.post("/api/settings/preisquelle/laden")
    p = integrations.price_guide("minifig", "sw0001a", "U")
    assert p["currency"] == "USD" and p["avg"] == "3.8500"


def test_unbekannter_kurs_bleibt_in_euro(client, hub):
    core.set_setting("price_region", "DE")
    core.set_setting("currency", "CHF")
    client.post("/api/settings/preisquelle/laden")
    p = integrations.price_guide("minifig", "sw0001a", "U")
    assert p["currency"] == "EUR" and p["avg"] == "3.5000"


def test_danach_stuendlich_nur_das_neue(client, hub, monkeypatch):
    """Einmal am Tag ganz, dazwischen `seit=<bis>` (Hub-PR #12)."""
    core.set_setting("price_region", "DE")
    jetzt = int(time.time())
    hub["abzug"] = _teilabzug([SATZ], teil=False, bis=jetzt - 600)
    assert hubpreise.laden("DE") == {"neu": 1, "teil": False}
    # Innerhalb der Stunde: gar nicht fragen.
    assert hubpreise.laden("DE") == {"uebersprungen": True}
    # Eine Stunde später: mit seit, nur das Neue; Altes bleibt stehen.
    st = json.loads(core.get_setting("hub_preise_stand"))
    st["DE"]["nach_am"] -= 3600
    core.set_setting("hub_preise_stand", json.dumps(st))
    neu = dict(SATZ, nr="sw0002", avg=9.0)
    hub["teil"] = _teilabzug([neu], teil=True, bis=jetzt)
    assert hubpreise.laden("DE") == {"neu": 1, "teil": True}
    url, params, kopf = hub["get"][-1]
    assert params == {"gebiet": "DE", "seit": str(jetzt - 600)}
    assert "if-none-match" not in kopf
    assert integrations.price_guide("minifig", "sw0001a", "U")["avg"] == "3.5000"
    assert integrations.price_guide("minifig", "sw0002", "U")["avg"] == "9.0000"
    # Nichts Neues: 304, und das nächste seit bleibt beim alten bis.
    hub["teil"] = None
    st = json.loads(core.get_setting("hub_preise_stand"))
    st["DE"]["nach_am"] -= 3600
    core.set_setting("hub_preise_stand", json.dumps(st))
    assert hubpreise.laden("DE") == {"unveraendert": True}
    assert json.loads(core.get_setting("hub_preise_stand"))["DE"]["bis"] == jetzt


def test_nach_einem_tag_wieder_ganz_mit_etag(client, hub):
    core.set_setting("price_region", "DE")
    hubpreise.laden("DE")
    st = json.loads(core.get_setting("hub_preise_stand"))
    assert st["DE"]["mit_alt"] is True
    st["DE"]["geholt_am"] -= hubpreise.ABZUG_ALTER
    core.set_setting("hub_preise_stand", json.dumps(st))
    assert hubpreise.laden("DE") == {"unveraendert": True}
    url, params, kopf = hub["get"][-1]
    assert params == {"gebiet": "DE", "mit_alt": "1"} and kopf["if-none-match"] == '"e1"'
    assert len(hub["post"]) == 1          # Zugang nur einmal angelegt


def test_erstes_mit_alt_ignoriert_altes_etag(client, hub):
    """Am 29.10.2026 fehlten alte Setdaten: der Hub gab wegen des alten ETags
    304 statt der erweiterten Antwort mit `mit_alt=1` zurück."""
    core.set_setting("price_region", "DE")
    core.set_setting("hub_preise_stand", json.dumps({
        "DE": {"etag": '"e1"',
               "geholt_am": int(time.time()) - hubpreise.ABZUG_ALTER - 1}
    }))
    alt = dict(SATZ, nr="sw0002", geholt_am=int(time.time()) - 30 * 86400,
               alt=True)
    set_alt = dict(SATZ, t="set", nr="75192-1", avg=600.0,
                   geholt_am=int(time.time()) - 30 * 86400, alt=True)
    hub["abzug"] = _abzug(SATZ, alt, set_alt)

    # Seit 3.9.31 behält auch die alte Figur ihren Preis.
    assert hubpreise.laden("DE") == {"neu": 3, "teil": False}
    _, params, kopf = hub["get"][-1]
    assert params == {"gebiet": "DE", "mit_alt": "1"}
    assert "if-none-match" not in kopf
    with core.db() as conn:
        assert conn.execute(
            "SELECT geholt_am FROM hub_preise_alt WHERE nr = 'sw0002'"
        ).fetchone()[0] == alt["geholt_am"]
        row = conn.execute(
            "SELECT avg, geholt_am FROM hub_preise "
            "WHERE t = 'set' AND nr = '75192-1'").fetchone()
        assert tuple(row) == (600.0, set_alt["geholt_am"])
    p = hubpreise.nachschlagen("set", "75192", "U", "DE", "EUR", "sold")
    assert p["avg"] == "600.0000" and p["geholt_am"] == set_alt["geholt_am"]
    f = hubpreise.nachschlagen("minifig", "sw0002", "U", "DE", "EUR", "sold")
    assert f["avg"] == "3.5000" and f["stand"]


def test_teil_ersetzt_und_altes_bleibt(client, hub):
    """Gleicher Schlüssel wird ersetzt; Älteres bleibt seit 3.9.31 stehen."""
    core.set_setting("price_region", "DE")
    jetzt = int(time.time())
    alt = dict(SATZ, nr="sw0003", geholt_am=jetzt - hubpreise.FRISCH_S + 60)
    hub["abzug"] = _teilabzug([SATZ, alt], teil=False, bis=jetzt - 600)
    hubpreise.laden("DE")
    st = json.loads(core.get_setting("hub_preise_stand"))
    st["DE"]["nach_am"] -= 3600
    core.set_setting("hub_preise_stand", json.dumps(st))
    with core.db() as conn:
        conn.execute("UPDATE hub_preise SET geholt_am = ? WHERE nr = 'sw0003'",
                     (jetzt - hubpreise.FRISCH_S - 60,))
    hub["teil"] = _teilabzug([dict(SATZ, avg=4.0, geholt_am=jetzt)], teil=True,
                             bis=jetzt)
    hubpreise.laden("DE")
    with core.db() as conn:
        zeilen = conn.execute("SELECT nr, avg FROM hub_preise WHERE gebiet = 'DE'"
                              ).fetchall()
    assert sorted(tuple(z) for z in zeilen) == [("sw0001a", 4.0), ("sw0003", 3.5)]


def test_nur_admins_schalten(client, hub):
    r = client.post("/api/settings/preisquelle/laden",
                    headers={"Authorization": client.ben})
    assert r.status_code == 403
    r = client.post("/api/settings/preisabruf", json={"stunden": 12},
                    headers={"Authorization": client.ben})
    assert r.status_code == 403
    assert client.post("/api/settings/preisabruf",
                       json={"stunden": 0}).status_code == 422


def test_preis_zugang_ist_geheim():
    assert "hub_preis_token" in integrations.GEHEIME_SETTINGS


def test_mit_schluessel_fragt_er_den_hub_nicht_mehr(client, hub, monkeypatch):
    core.set_setting("price_region", "DE")
    client.post("/api/settings/preisquelle/laden")
    _schluessel_eintragen()
    assert not hubpreise.an()
    gerufen = []
    monkeypatch.setattr(integrations, "_price_request",
                        lambda *a, **k: gerufen.append(a) or {})
    integrations.price_guide("minifig", "sw0001a", "U")
    assert gerufen                       # wieder BrickLink


def test_gross_getippte_nummer_findet_den_kleinen_preis(client, hub):
    """Hub 1.35.0 speichert Nummern klein; „SW0001A“ muss ihn trotzdem finden."""
    core.set_setting("price_region", "DE")
    client.post("/api/settings/preisquelle/laden")
    p = client.get("/api/price/minifig/SW0001A").json()
    assert p["used"]["avg"] == "3.5000"


def test_ohne_preis_im_eigenen_gebiet_hilft_der_kontinent_dann_deutschland(client, hub):
    """App-Store-Vorbereitung (12.10.2026): Länder und Kontinente gibt es nur,
    wo verkauft wurde. Eine Instanz in Australien sieht trotzdem einen Preis –
    erst aus Ozeanien, sonst weltweit – und erkennt, woher er stammt."""
    import hubpreise
    assert hubpreise.kette("AU") == ["AU", "oceania", ""]
    assert hubpreise.kette("asia") == ["asia", ""]
    assert hubpreise.kette("DE") == ["DE", "europe", ""]
    assert hubpreise.kette("") == [""]
    core.set_setting("price_region", "AU")
    hub["andere_abzug"][""] = _abzug(dict(SATZ, gebiet="", genutzt=""))
    client.post("/api/settings/preisquelle/laden")
    assert hub["andere"] == ["AU", "oceania", ""]
    p = integrations.price_guide("minifig", "sw0001a", "U")
    assert p["avg"] == "3.5000" and p["used_scope"] == ""   # weltweit
    assert p["scope"] == "AU" and p["fell_back"] is True
    # Hat Ozeanien selbst einen Preis, gewinnt er.
    hub["andere_abzug"]["oceania"] = _abzug(dict(SATZ, gebiet="oceania",
                                                 genutzt="oceania", avg=4.0))
    client.post("/api/settings/preisquelle/laden")
    p = integrations.price_guide("minifig", "sw0001a", "U")
    assert p["avg"] == "4.0000" and p["used_scope"] == "oceania"


def test_eigener_preis_hub_neben_dem_tausch_hub(client, hub, monkeypatch):
    """`PREIS_HUB_URL` lenkt nur die Preise um, das Tausch-Netz bleibt."""
    import hub as tausch
    core.set_setting("price_region", "DE")
    monkeypatch.setenv("PREIS_HUB_URL", "http://preise.test:8787/")
    hubpreise.laden("DE", erzwingen=True)
    assert hub["get"][-1][0] == "http://preise.test:8787/v1/preise/abzug"
    assert hub["post"][-1] == "http://preise.test:8787/v1/preise/zugang"
    assert tausch.HUB_URL != "http://preise.test:8787"


def test_waechter_laedt_rueckfaelle_auch_wenn_das_eigene_gebiet_scheitert(monkeypatch):
    """Ein einziges try um die ganze Kette: Scheiterte das eigene Gebiet,
    blieben Kontinent und weltweit ungeladen – gerade die Rückfälle, die
    dann gebraucht werden (Durchsicht 17.10.2026)."""
    geladen = []

    def laden(g, erzwingen=False):
        if g == "DE":
            raise RuntimeError("Hub weg")
        geladen.append(g)
        return {}

    class Ende(Exception):
        pass

    def schlafen(s):
        if s == 3600:
            raise Ende

    monkeypatch.setattr(hubpreise, "an", lambda: True)
    monkeypatch.setattr(hubpreise, "kette", lambda g: ["DE", "EU", ""])
    monkeypatch.setattr(hubpreise, "laden", laden)
    monkeypatch.setattr(hubpreise.time, "sleep", schlafen)
    with pytest.raises(Ende):
        hubpreise.waechter(lambda: "DE")
    assert geladen == ["EU", ""]


# ------------------------------------------ Hinweis mit Datum (3.9.27)

def test_ohne_frischen_preis_kommt_das_datum_nie_der_preis(client, hub):
    """22.10.2026: „Preis zuletzt am … geholt“ statt „Kein aktueller
    Preis“ – der alte Preis selbst erscheint nie (24-h-Regel)."""
    core.set_setting("price_region", "DE")
    alt = int(time.mktime((2026, 10, 21, 12, 0, 0, 0, 0, -1)))
    hub["abzug"] = _abzug(SATZ, {"t": "minifig", "nr": "sw0053", "z": "U",
                                 "art": "sold", "gebiet": "DE",
                                 "geholt_am": alt, "alt": True})
    client.post("/api/settings/preisquelle/laden")
    r = client.get("/api/price/minifig/sw0053")
    assert r.status_code == 404
    assert r.json()["detail"] == "Preis zuletzt am 21.10.2026 geholt, wird bald aktualisiert."
    assert "avg" not in r.text


def test_veralteter_satz_behaelt_seinen_preis(client, hub):
    """Eine Testsammlung am 30.10.2026: 173 statt 161 Einträge ohne Preis, weil
    Figurenpreise nach 26 Stunden gelöscht wurden. Seit 3.9.31 bleibt der
    Preis stehen und nennt ab 48 Stunden seinen Stand."""
    core.set_setting("price_region", "DE")
    client.post("/api/settings/preisquelle/laden")
    with core.db() as conn:
        conn.execute("UPDATE hub_preise SET geholt_am = ?",
                     (int(time.time()) - 50 * 3600,))
    hubpreise._alte_weg("DE")
    p = hubpreise.nachschlagen("minifig", "sw0001a", "U", "DE", "EUR", "sold")
    assert p["avg"] == "3.5000" and p["stand"]
    with core.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hub_preise").fetchone()[0] == 1


def test_ohne_jedes_datum_bleibt_der_alte_satz(client, hub):
    core.set_setting("price_region", "DE")
    client.post("/api/settings/preisquelle/laden")
    r = client.get("/api/price/minifig/sw9999")
    assert r.json()["detail"] == "Kein aktueller Preis im Preisdienst."


# ------------------------------------------- Nur für Nupplo (3.9.32)

def test_zeichen_stimmt_mit_dem_testvektor():
    k = hubpreise.zeichen("GET", "/v1/preise/abzug", zeit=1793350000,
                          fassung="3.9.27")
    assert k == {"x-nupplo-programm": "se/3.9.27", "x-nupplo-zeit": "1793350000",
                 "x-nupplo-zeichen": "a1dc22b9cc16501f6edc2e7c7327024c555d28fc975f215e688cb23e10fd21d2"}
    ios = hubpreise.zeichen("POST", "/v1/preise/zugang", zeit=1793350000,
                            programm="ios", fassung="0.53.0",
                            schluessel="QOmpg5cRuEAVm9HnrURwPYUZYXSRMfv5YQJvKKSKUKg=")
    assert ios["x-nupplo-zeichen"] == \
        "74311d688a731473dec6495b76de8b0e770c517a05adeece987af340240f1c0d"


def test_jeder_abruf_traegt_das_zeichen(client, hub):
    core.set_setting("price_region", "DE")
    hubpreise.laden("DE", erzwingen=True)
    _, _, kopf = hub["get"][-1]
    assert kopf["x-nupplo-programm"].startswith("se/")
    assert len(kopf["x-nupplo-zeichen"]) == 64 and kopf["x-nupplo-zeit"].isdigit()


def test_notbremse_ist_kein_fehler_und_preise_bleiben(client, hub, monkeypatch):
    core.set_setting("price_region", "DE")
    hubpreise.laden("DE", erzwingen=True)
    jetzt = int(time.time())
    bremse = Antwort(503, data={"abgeschaltet": True, "zeit": jetzt + 3600},
                     headers={"retry-after": "1800"})
    monkeypatch.setattr(hubpreise.requests, "get", lambda *a, **k: bremse)
    assert hubpreise.laden("DE", erzwingen=True) == {"abgeschaltet": True}
    assert hubpreise.ruht()
    assert int(core.get_setting("hub_uhr_versatz")) >= 3590    # Uhr nachgestellt
    assert hubpreise.laden("DE") in ({"abgeschaltet": True},
                                     {"uebersprungen": True})    # kein Abruf
    p = hubpreise.nachschlagen("minifig", "sw0001a", "U", "DE", "EUR", "sold")
    assert p["avg"] == "3.5000"


def test_wasserzeichen_geht_nicht_weiter(client, hub):
    core.set_setting("price_region", "DE")
    hub["abzug"] = _abzug(dict(SATZ, avg=3.4567891))
    hubpreise.laden("DE", erzwingen=True)
    with core.db() as conn:
        assert conn.execute("SELECT avg FROM hub_preise").fetchone()[0] == 3.4567891
    p = hubpreise.nachschlagen("minifig", "sw0001a", "U", "DE", "EUR", "sold")
    assert p["avg"] == "3.4600"
