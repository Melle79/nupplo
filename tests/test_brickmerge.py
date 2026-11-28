"""Brickmerge im Set-Steckbrief: Angebotspreis beim Öffnen, Setseite lesen.

Anlass (10.10.2026): „erscheinung und eol sind schon sehr wichtig“, und
Sven wollte den Preis „vom gerät abrufen lassen, wenn man auf den
Steckbrief geht und dann den link auf die Brickmegre-seite machen“. Ein
Schlüssel für Brickset kam nicht in Frage („von den keys wollten wir ja
weg“). Die Texte unten sind gekürzt von den Setseiten vom 10.10.2026.
"""
import time
from pathlib import Path

import pytest
import requests

import brickmerge
import core
import main
from fastapi.testclient import TestClient

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

FALKE = """<html><body><div>EAN: 5702015869935 | Händler-VE: 1 /Karton
 <a>Korrektur melden</a> | <b>Release:</b> 10/2017 , <b>EOL:</b> 12/2026 ,
 PLC: 111 Monate | urspr. UVP: 799,99 &euro; | akt. UVP: 849,99 &euro;
 (11,27 ct/Teil)</div><script>var x = "ab 1,00 €";</script>
 <p>30 Tage Bestpreis: 715,24 € / 16% gestern | akt. brickmerge Preis:
 <span>ab 715,24 €</span> / 16%</p></body></html>"""
ALT = """<div>EAN: 5702012006548 | Release: 08/2000 , EOL: 02/2002 08/00
 02/02 heute 576 T.</div><p>Teile: 659 | Minifiguren: 6</p>"""
NEU = """<div>Release: 01/2026 , EOL: ≈2028 , PLC: ≈27 Monate | UVP: 1.249,99 €
 </div><p>kostet aktuell ab 1.099,00 € * (zzgl. Versand)</p>"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "bm.db"))
    core.init_db()
    with core.db() as conn:
        conn.execute("INSERT INTO users (username, password_hash, is_admin,"
                     " is_dealer, created_at) VALUES ('anna', 'x', 1, 1, ?)",
                     (int(time.time()),))
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(1, "anna", True)
    return c


def _seite(monkeypatch, text="", url=None, fehler=None):
    gefragt = []

    class Antwort:
        status_code = 200

        def __init__(self, adresse):
            self.url = url or adresse + "_lego-irgendwas"
            self.text = text

        def raise_for_status(self):
            pass

    def get(adresse, **kw):
        gefragt.append(adresse)
        if fehler:
            raise fehler
        return Antwort(adresse)

    monkeypatch.setattr(brickmerge.requests, "get", get)
    return gefragt


def test_setseite_liefert_preis_uvp_erscheinung_und_eol():
    assert brickmerge.lesen(FALKE) == {
        "erschienen": "2017-10", "eol": "2026-12",
        "uvp_eur": 849.99, "ab_eur": 715.24}


def test_ausgelaufen_ohne_preis_und_uvp():
    """Bei 7190-1 stehen nur Release und EOL da – kein Preis ist kein
    Fehler und schon gar nicht 0 €."""
    assert brickmerge.lesen(ALT) == {"erschienen": "2000-08", "eol": "2002-02"}


def test_geschaetztes_eol_und_tausenderpunkt():
    assert brickmerge.lesen(NEU) == {
        "erschienen": "2026-01", "eol": "2028", "eol_geschaetzt": True,
        "uvp_eur": 1249.99, "ab_eur": 1099.0}


def _zustimmen(client):
    assert client.post("/api/settings/brickmerge_preise",
                       json={"an": True}).json()["an"]


def test_je_set_und_tag_nur_einmal_geholt(client, monkeypatch):
    _zustimmen(client)
    gefragt = _seite(monkeypatch, FALKE)
    for _ in range(3):
        d = client.get("/api/brickmerge/75192").json()
    assert gefragt == ["https://www.brickmerge.de/75192-1"]
    assert d["ab_eur"] == 715.24
    assert d["url"].startswith("https://www.brickmerge.de/75192-1_")


def test_fehler_zeigt_still_nur_den_link(client, monkeypatch):
    _zustimmen(client)
    gefragt = _seite(monkeypatch, fehler=requests.ConnectionError("weg"))
    d = client.get("/api/brickmerge/75192-1").json()
    assert d == {"url": "https://www.brickmerge.de/75192-1"}
    # Nicht bei jedem Öffnen erneut gegen die Wand
    client.get("/api/brickmerge/75192-1")
    assert len(gefragt) == 1


def test_unbekannte_nummer_nimmt_nichts_von_der_startseite(client, monkeypatch):
    """Brickmerge leitet Unbekanntes auf die Startseite – deren „ab … €“
    gehört zu irgendeinem anderen Set."""
    _zustimmen(client)
    _seite(monkeypatch, NEU, url="https://www.brickmerge.de/")
    assert "ab_eur" not in client.get("/api/brickmerge/99999-1").json()


def test_eigene_sets_fragen_nicht(client, monkeypatch):
    _zustimmen(client)
    gefragt = _seite(monkeypatch, FALKE)
    assert client.get("/api/brickmerge/custom-123").status_code == 404
    assert gefragt == []


def test_oberflaeche_fragt_brickmerge_nur_eingeschaltet():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    teil = js[js.index("async function setdatenEinsetzen"):]
    teil = teil[:teil.index("\n}\n")]
    assert "state.brickmergePreise\n      ? api(`/brickmerge/" in teil
    assert 'zeile("Bei Brickmerge"' in js


BILDER = """<script type="application/ld+json">{"@context":"https://schema.org",
 "@type":"Product","name":"LEGO Star Wars 75192 Millennium Falke",
 "image":["https://www.brickmerge.de/img/sets/l/LEGO_75192.jpg",
          "https://www.brickmerge.de/img/sets/l/LEGO_75192_alt1.jpg",
          "https://irgendwo.example/fremd.jpg"]}</script>
<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>"""


def test_setbilder_aus_der_seite():
    """Anlass: „brickmerge hat auch die Bilder der sets. könnte man diese
    auch anzeigen lassen?“ (10.10.2026). Genommen wird nur
    Brickmerges eigener Bildordner – der Server holt die Bilder selbst."""
    assert brickmerge.lesen(BILDER)["bilder"] == [
        "https://www.brickmerge.de/img/sets/l/LEGO_75192.jpg",
        "https://www.brickmerge.de/img/sets/l/LEGO_75192_alt1.jpg"]
    assert "bilder" not in brickmerge.lesen(ALT)


def test_setbilder_laedt_der_browser_nicht_der_server():
    """Sven: „Die Bilder bitte nicht mit ziehen, diese sollen von dort aus
    geöffnet werden.“ Bis 3.9.12 holte der Bildspeicher sie und behielt
    eine Kopie. Jetzt hat der Server keinen Weg mehr dorthin, und die
    Seite erlaubt dem Browser, sie bei brickmerge.de zu laden."""
    import integrations
    assert "www.brickmerge.de" not in integrations.BILD_HOSTS
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    teil = js[js.index("function setbilderEinsetzen"):]
    teil = teil[:teil.index("\n}\n")]
    assert "imgSrc(" not in teil and 'referrerpolicy="no-referrer"' in teil


def test_setbilder_nur_eingeschaltet(client):
    """„Aber auch abschaltbar bitte.“ Voreinstellung aus wie beim Preis;
    aus heißt keine Reihe und keine Anfrage bei Brickmerge."""
    assert client.get("/api/config").json()["brickmerge_bilder"] is False
    assert client.post("/api/settings/brickmerge_bilder",
                       json={"an": True}).json()["an"]
    assert client.get("/api/config").json()["brickmerge_bilder"] is True
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "if (state.brickmergeBilder) setbilderEinsetzen(ziel, d.bilder" in js
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="opt-brickmerge-bilder"' in html


def test_seite_erlaubt_brickmerge_bilder(client):
    r = client.get("/")
    assert "https://www.brickmerge.de" in r.headers["content-security-policy"]


def test_oberflaeche_zeigt_die_bilderreihe():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "setbilderEinsetzen(ziel, d.bilder" in js


def test_angebotspreis_nur_mit_zustimmung(client, monkeypatch):
    """Anlass: „die angebotspreise von brickmerge sollten, genauso wie die
    ab-Preise von Bricklink optional sein“ (10.10.2026). Seit 3.9.12
    kommt alles andere aus der Setdatei („die setdaten ziehen wir uns
    selber und diese werden im katalog hinterlegt“) – ohne Zustimmung geht
    beim Öffnen gar nichts mehr nach draußen."""
    gefragt = _seite(monkeypatch, FALKE)
    d = client.get("/api/brickmerge/75192-1").json()
    assert d == {"url": "https://www.brickmerge.de/75192-1"}
    assert gefragt == []
    assert client.get("/api/config").json()["brickmerge_preise"] is False
    _zustimmen(client)
    assert client.get("/api/config").json()["brickmerge_preise"] is True
    d = client.get("/api/brickmerge/75192-1").json()
    assert d["ab_eur"] == 715.24 and "uvp_eur" not in d
    assert len(gefragt) == 1


def test_oberflaeche_zeigt_brickmerge_preis_nur_eingeschaltet():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "d.brickmerge_url && state.brickmergePreise" in js
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="opt-brickmerge-preise"' in html
