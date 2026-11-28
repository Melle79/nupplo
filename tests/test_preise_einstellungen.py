"""Einstellungen rund um Preise, seit es zwei Wege gibt (10.10.2026).

Anlass: Sven ließ die Einstellungen gegen den neuen Fahrplan durchsehen –
Preise vom Nupplo-Preisdienst oder mit eigenem BrickLink-Zugang, beides soll
bleiben. Dabei fiel auf, dass die Seite in vielem noch so tat, als gäbe es
nur BrickLink: Die Kontaktadresse wurde jedem abverlangt, der Pflichthinweis
„This application uses the BrickLink API“ stand auch ohne Schlüssel da, und
für unbeaufsichtigte Einrichtungen ließ sich der Preisdienst nicht vorab
einschalten.
"""
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core
import hubpreise
import integrations
import main

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "pe.db"))
    monkeypatch.delenv("PREISQUELLE", raising=False)
    for name in integrations.SETTING_ENV.values():
        monkeypatch.delenv(name, raising=False)
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at)"
            " VALUES ('anna', 'x', 1, ?)", (int(time.time()),)).lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    return c


def test_der_schluessel_entscheidet_nicht_die_umgebung(client, monkeypatch):
    """Seit 3.9.17 entweder – oder: Ohne eigenen Schlüssel der Preisdienst,
    mit ihm BrickLink. `PREISQUELLE` aus der Umgebung zählt nicht mehr."""
    monkeypatch.setenv("PREISQUELLE", "bricklink")
    assert hubpreise.an() is True
    assert client.get("/api/config").json()["preisquelle"] == "hub"
    for k in integrations.BL_KEYS:
        core.set_setting(k, "x")
    assert hubpreise.an() is False
    assert client.get("/api/config").json()["preisquelle"] == "bricklink"


def test_verbindungstest_meldet_den_preisdienst_nur_ohne_schluessel(client):
    """Mit eigenem Schlüssel soll der Test kein rotes Kreuz für einen Dienst
    zeigen, den man gar nicht nutzt – mit dem Preisdienst aber sagen, ob
    schon Preise da sind."""
    r = client.post("/api/settings/test").json()
    assert r["preisdienst"] == {"ok": False,
                                "info": "Noch keine Preise geladen"}
    for k in integrations.BL_KEYS:
        core.set_setting(k, "x")
    r = client.post("/api/settings/test").json()
    assert "preisdienst" not in r


def test_kontaktadresse_steht_beim_eigenen_brickLink_zugang():
    """Die Adresse verlangen nur BrickLinks API-Bedingungen. Sie gehört zum
    eigenen Zugang und blendet sich nach dem Schlüssel ein, nicht nach
    „Preise möglich“ – das ist mit dem Preisdienst auch ohne Schlüssel wahr."""
    html = (FRONTEND / "index.html").read_text()
    zugang = re.search(r'<details class="help" id="bl-zugang">(.*?)</details>',
                       html, re.S).group(1)
    assert 'id="k-kontakt"' in zugang and 'id="kontakt-block" hidden' in zugang
    js = (FRONTEND / "app.js").read_text()
    kontakt = re.search(r"function kontaktZeigen\(\) \{(.*?)\n\}", js, re.S).group(1)
    assert "state.bricklinkLookup" in kontakt
    assert "state.bricklinkPrices" not in kontakt


def test_pflichthinweis_nur_mit_eigenem_schluessel():
    """„This application uses the BrickLink API“ stimmt im Preisdienst-Betrieb
    ohne Schlüssel nicht. 3.9.0 hängte den Hinweis an `bricklinkPrices`, und
    das ist seit dem Preisdienst auch ohne Schlüssel wahr."""
    js = (FRONTEND / "app.js").read_text()
    teil = re.search(r"function rechtlichesAktualisieren\(\) \{(.*?)\n\}",
                     js, re.S).group(1)
    assert 'block.hidden = !state.bricklinkLookup' in teil


def test_preisdienst_zeile_im_verbindungstest_wird_uebersetzt(client):
    """In 3.9.1 kam „12345 Preise geladen“ als fertiger Satz vom Server. Mit
    der Zahl darin fand ihn das Wörterbuch nie, auf Englisch stand er
    deshalb deutsch da. Die Zahl kommt jetzt einzeln mit."""
    core.set_setting("preisquelle", "hub")
    with core.db() as conn:
        hubpreise._tabelle(conn)
        conn.execute("INSERT INTO hub_preise (t, nr, z, art, gebiet, avg)"
                     " VALUES ('minifig', 'sw0001a', 'U', 'sold', 'DE', 4.2)")
    r = client.post("/api/settings/test").json()
    assert r["preisdienst"]["anzahl"] == 1
    js = (FRONTEND / "app.js").read_text()
    teil = re.search(r"function testZeilen\(r\) \{(.*?)\n\}", js, re.S).group(1)
    assert 'tr("{n} Preise geladen", { n: t.anzahl })' in teil
