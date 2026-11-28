"""Teile- und Setdatei kommen vom Hub, nicht von GitHub (3.9.25).

Anlass, 17.10.2026: Die SE war auf GitHub-Pfade eingestellt, die nie
jemand füllte – ohne die Probe-Adresse gab es keine Teile, keine
Doppelgänger, keine Setdaten. Sven wollte die Dateien nicht auf GitHub;
sie liegen jetzt wie die Preise im Hub (1.41.0), abgeholt mit dem
anonymen Preis-Zugang.
"""
import json
import time

import pytest

import core
import figurteile
import hubpreise
import main


class Antwort:
    def __init__(self, status, text="", etag=None):
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.headers = {"ETag": etag} if etag else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "k.db"))
    core.init_db()
    monkeypatch.delenv("FIGURTEILE_QUELLE", raising=False)
    monkeypatch.delenv("SETDATEN_QUELLE", raising=False)


@pytest.fixture
def hub(monkeypatch):
    gefragt = []
    antworten = {}

    def get(url, headers=None, **kw):
        gefragt.append((url, dict(headers or {})))
        return antworten.get(url, Antwort(404))
    monkeypatch.setattr(hubpreise.requests, "get", get)
    return gefragt, antworten


TEILE = json.dumps({"item_no": "sw0001", "teile": [["3626b", 1, 1]], "dg": []}) + "\n"
SETS = json.dumps({"nr": "7190-1", "name": "Falcon", "jahr": 2000, "teile": 671}) + "\n"


def test_teiledatei_vom_hub_mit_zugang(db, hub):
    gefragt, antworten = hub
    antworten["http://hub.test/v1/katalog/figurteile"] = Antwort(200, TEILE, '"t1"')
    assert figurteile.ziehen()["geholt"] == 1
    url, kopf = gefragt[-1]
    assert url == "http://hub.test/v1/katalog/figurteile"
    assert kopf["Authorization"] == "Bearer prz_test"
    antworten[url] = Antwort(304)
    figurteile.ziehen()
    assert gefragt[-1][1]["If-None-Match"] == '"t1"'


def test_setdatei_vom_hub(db, hub):
    gefragt, antworten = hub
    antworten["http://hub.test/v1/katalog/setdaten"] = Antwort(200, SETS, '"s1"')
    assert main._setdaten_ziehen()["geholt"] == 1
    assert gefragt[-1][0] == "http://hub.test/v1/katalog/setdaten"


def test_eigene_adresse_geht_vor(db, hub, monkeypatch):
    """Die Testinstanz zeigt auf den Probe-Katalogdienst im LAN."""
    gefragt, antworten = hub
    monkeypatch.setenv("FIGURTEILE_QUELLE", "http://probe:8321/api/figurteile.ndjson")
    antworten["http://probe:8321/api/figurteile.ndjson"] = Antwort(200, TEILE)
    figurteile.ziehen()
    assert gefragt[-1][0] == "http://probe:8321/api/figurteile.ndjson"
    assert "Authorization" not in gefragt[-1][1]


def test_abgelehnter_zugang_wird_einmal_neu_angelegt(db, monkeypatch):
    """Nach einem Neuaufbau des Hubs kennt er den gemerkten Zugang nicht mehr."""
    zugaenge = iter(["prz_alt", "prz_neu"])
    core.set_setting("hub_preis_token", "prz_alt")
    monkeypatch.setattr(hubpreise, "_token",
                        lambda: core.get_setting("hub_preis_token") or next(zugaenge))
    next(zugaenge)
    benutzt = []

    def get(url, headers=None, **kw):
        benutzt.append(headers["Authorization"])
        return Antwort(401) if headers["Authorization"] == "Bearer prz_alt" \
            else Antwort(200, TEILE)
    monkeypatch.setattr(hubpreise.requests, "get", get)
    assert figurteile.ziehen()["geholt"] == 1
    assert benutzt == ["Bearer prz_alt", "Bearer prz_neu"]
