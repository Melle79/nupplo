"""Sets unter ihrem deutschen Namen finden.

Anlass (10.10.2026): „Supersternzerstörer Executor" fand im manuellen
Erfassen nichts. BrickLink kennt das Set nur als „Executor Super Star
Destroyer - UCS", Rebrickable ähnlich – und das Wörterbuch übersetzt nicht
jeden Namen in LEGOs Wortstellung. Die deutschen Namen holt der
Katalogdienst von LEGO und legt sie als `setnamen.ndjson` neben den Abzug.
"""
import json
import time

import pytest
import requests

import core
import integrations
import main
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "sn.db"))
    core.init_db()
    with core.db() as conn:
        conn.execute("INSERT INTO users (username, password_hash, is_admin,"
                     " is_dealer, created_at) VALUES ('anna', 'x', 1, 1, ?)",
                     (int(time.time()),))
    # Ohne Rebrickable: genau der Fall, in dem es bisher gar nichts gab.
    monkeypatch.setattr(integrations, "rebrickable_enabled", lambda: False)
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(1, "anna", True)
    return c


def _datei(monkeypatch, zeilen, status=200, etag='"n1"'):
    inhalt = "".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen)

    class Fake:
        status_code = status
        headers = {"ETag": etag} if etag else {}
        content = inhalt.encode()
        text = inhalt

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    gefragt = []
    monkeypatch.setattr(main.requests, "get",
                        lambda url, **kw: gefragt.append((url, kw)) or Fake())
    return gefragt


NAMEN = [{"nr": "75457-1", "sprache": "de", "name": "Supersternzerstörer Executor"},
         {"nr": "75192-1", "sprache": "de", "name": "Millennium Falke"},
         {"nr": "60316-1", "sprache": "de", "name": "Polizeistation"},
         {"nr": "75192-1", "sprache": "fr", "name": "Le Faucon Millennium"}]


def _suche(client, q):
    r = client.get("/api/search", params={"q": q, "item_type": "set"})
    assert r.status_code == 200
    return r.json()


def test_executor_unter_seinem_deutschen_namen(client, monkeypatch):
    _datei(monkeypatch, NAMEN)
    assert main._setnamen_ziehen()["geholt"] == 4
    d = _suche(client, "Supersternzerstörer Executor")
    assert [i["item_id"] for i in d["items"]] == ["75457-1"]
    assert d["items"][0]["name"] == "Supersternzerstörer Executor"
    assert d["items"][0]["item_type"] == "set"
    # Kein „nichts gefunden" – sonst liefe die Übersetzung obendrauf.
    assert d["eigene_leer"] is False


def test_wortteil_im_zusammengesetzten_namen(client, monkeypatch):
    """„Sternzerstörer" beginnt in „Supersternzerstörer" kein Wort. Deutsche
    Setnamen kleben zusammen – lange Wörter zählen deshalb auch mittendrin,
    kurze nicht („turm" soll keinen „Sturm" finden)."""
    _datei(monkeypatch, NAMEN)
    main._setnamen_ziehen()
    assert [i["item_id"] for i in _suche(client, "sternzerstörer")["items"]] \
        == ["75457-1"]
    assert _suche(client, "tion")["items"] == []


def test_ein_set_in_zwei_sprachen_steht_einmal_da(client, monkeypatch):
    _datei(monkeypatch, NAMEN)
    main._setnamen_ziehen()
    assert [i["item_id"] for i in _suche(client, "millennium")["items"]] \
        == ["75192-1"]
    assert [i["item_id"] for i in _suche(client, "faucon")["items"]] \
        == ["75192-1"]


def test_figurensuche_bleibt_unberuehrt(client, monkeypatch):
    _datei(monkeypatch, NAMEN)
    main._setnamen_ziehen()
    r = client.get("/api/search", params={"q": "Millennium Falke",
                                          "item_type": "minifig"})
    assert r.json().get("items", []) == []


def test_ohne_datei_bleibt_es_beim_alten(client, monkeypatch):
    """Bis der Katalogdienst die Datei veröffentlicht, gibt es sie nicht.
    Das ist kein Fehler, und es löscht nichts."""
    _datei(monkeypatch, NAMEN)
    main._setnamen_ziehen()
    _datei(monkeypatch, [], status=404)
    assert main._setnamen_ziehen()["grund"] == "noch keine Datei"
    _datei(monkeypatch, [])
    assert main._setnamen_ziehen()["grund"] == "leer"
    assert _suche(client, "Polizeistation")["items"][0]["item_id"] == "60316-1"


def test_unveraendert_kostet_nur_eine_rueckfrage(client, monkeypatch):
    _datei(monkeypatch, NAMEN, etag='"v7"')
    main._setnamen_ziehen()
    gefragt = _datei(monkeypatch, [], status=304)
    assert main._setnamen_ziehen()["grund"] == "unverändert"
    assert gefragt[0][1]["headers"]["If-None-Match"] == '"v7"'


def test_testinstanz_holt_vom_probe_dienst(client, monkeypatch):
    """Solange die Datei nicht veröffentlicht ist, soll die Testinstanz sie
    direkt vom Probe-Katalogdienst im LAN holen können – ohne Eingriff in
    die Datenbank (10.10.2026, vor dem großen Update)."""
    monkeypatch.setenv("SETNAMEN_QUELLE",
                       "http://192.168.0.199:8321/api/setnamen.ndjson")
    gefragt = _datei(monkeypatch, NAMEN)
    main._setnamen_ziehen()
    assert gefragt[0][0] == "http://192.168.0.199:8321/api/setnamen.ndjson"
