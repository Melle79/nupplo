"""Desktop-Modus für Nupplo für Windows (16.10.2026).

Anlass: Die Windows-App soll ohne NAS laufen. Sie startet dieselbe SE auf
dem eigenen Rechner und meldet ihr Fenster ohne Passwort an – über ein
Einmal-Geheimnis, das nur das Programm kennt. Diese Abkürzung darf auf einem
NAS nie offen sein und auf dem Rechner nur für das eigene Programm: Ein
anderes Programm, das den Port findet, oder ein Gerät im Netz bekäme sonst
die Sammlung samt Eigner-Rechten.
"""
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import main

REPO_WURZEL = pathlib.Path(__file__).resolve().parent.parent

GEHEIMNIS = "einmal-geheimnis-fuer-den-test"


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    monkeypatch.setenv("NUPPLO_DESKTOP_GEHEIMNIS", GEHEIMNIS)


def _lokal():
    return TestClient(main.app, client=("127.0.0.1", 50000))


def test_auf_dem_nas_gibt_es_keine_abkuerzung(monkeypatch):
    """Ohne NUPPLO_DESKTOP darf auch das richtige Geheimnis nichts öffnen –
    eine NAS-Instanz, der jemand die Variable mitgibt, bleibt zu."""
    monkeypatch.delenv("NUPPLO_DESKTOP", raising=False)
    monkeypatch.setenv("NUPPLO_DESKTOP_GEHEIMNIS", GEHEIMNIS)
    r = _lokal().get("/desktop/start", params={"code": GEHEIMNIS})
    assert r.status_code == 404
    assert _lokal().get("/api/desktop").json() == {"desktop": False}


def test_ohne_geheimnis_bleibt_es_zu(monkeypatch):
    """Ein leeres Geheimnis passt auf einen leeren Code – das wäre offen."""
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    monkeypatch.delenv("NUPPLO_DESKTOP_GEHEIMNIS", raising=False)
    assert _lokal().get("/desktop/start", params={"code": ""}).status_code == 404


def test_falscher_code(desktop):
    r = _lokal().get("/desktop/start", params={"code": "geraten"})
    assert r.status_code == 403
    assert "bf_token" not in r.text


def test_nur_vom_eigenen_rechner(desktop):
    """Lauscht die SE versehentlich im Netz, bekommt ein anderes Gerät auch
    mit dem Geheimnis kein Token."""
    fremd = TestClient(main.app, client=("192.168.0.50", 50000))
    r = fremd.get("/desktop/start", params={"code": GEHEIMNIS})
    assert r.status_code == 403
    assert "bf_token" not in r.text


def test_richtiger_code_meldet_das_fenster_an(desktop):
    """Das Token aus der Startseite muss für die Web-App taugen – sonst
    landet das Fenster doch auf der Anmeldung."""
    with _lokal() as c:                     # mit Startfunktionen: Konto da
        assert c.get("/api/desktop").json() == {"desktop": True}
        r = c.get("/desktop/start", params={"code": GEHEIMNIS})
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        token = re.search(r"'bf_token', \"([^\"]+)\"", r.text).group(1)
        me = c.get("/api/me", headers={"Authorization": "Bearer " + token})
        assert me.status_code == 200
        assert me.json()["is_admin"] is True


def test_startseite_darf_ihr_skript_ausfuehren(desktop):
    """Beim ersten Probelauf blieb das Fenster weiß auf /desktop/start: Die
    allgemeine CSP verbietet Inline-Skripte, das Token kam nie an."""
    r = _lokal().get("/desktop/start", params={"code": GEHEIMNIS})
    nonce = re.search(r"<script nonce='([^']+)'>", r.text).group(1)
    csp = r.headers["content-security-policy"]
    assert f"script-src 'nonce-{nonce}'" in csp
    assert "'self'" not in csp.split("script-src")[1].split(";")[0]


def test_bf_user_ist_json(desktop):
    """Die Web-App liest bf_user mit JSON.parse. Ein bloßer Name darin
    („Sammler“) wirft beim Laden und zerlegt die ganze App."""
    import json
    r = _lokal().get("/desktop/start", params={"code": GEHEIMNIS})
    roh = r.text.split("'bf_user', ", 1)[1].split(");location", 1)[0]
    gespeichert = json.loads(roh)            # was setItem ablegt
    assert isinstance(json.loads(gespeichert), dict)


def test_assistent_nur_beim_ersten_fenster(desktop):
    """Auf dem NAS startet der Einrichtungsassistent nach dem Anlegen des
    Kontos. Am eigenen Rechner legt das Programm das Konto selbst an – ohne
    diesen Hinweis sähe niemand je Preise, BrickLink-Zugang und Tauschbörse
    einrichten. Danach aber nicht bei jedem Start wieder."""
    import core
    core.set_setting("desktop_willkommen", "")
    erst = _lokal().get("/desktop/start", params={"code": GEHEIMNIS}).text
    dann = _lokal().get("/desktop/start", params={"code": GEHEIMNIS}).text
    assert 'location.replace("/?willkommen=1")' in erst
    assert 'location.replace("/")' in dann


def test_rechner_wird_keine_connect_instanz(desktop):
    """Svens Entscheidung vom 16.10.2026: Nupplo für Windows verbindet sich
    über den eigenen Cloud-Ordner oder über Connect mit einer SE – es wird
    selbst keine Instanz, an die sich Geräte koppeln."""
    import core
    core.set_setting("connect_an", "0")
    r = _lokal().get("/desktop/start", params={"code": GEHEIMNIS})
    token = re.search(r"'bf_token', \"([^\"]+)\"", r.text).group(1)
    an = _lokal().post("/api/connect", json={"an": True},
                       headers={"Authorization": "Bearer " + token})
    assert an.status_code == 409
    assert core.get_setting("connect_an") != "1"


def test_mit_der_se_gewaehlt_geht_es_zum_abgleich(desktop):
    """Wer beim ersten Start „Mit meiner Nupplo SE“ wählt, soll gleich die
    Anmeldung an der SE sehen – nicht erst sechs Schritte Assistent, nach
    denen er vergessen hat, warum er hier ist."""
    import core
    core.set_setting("desktop_willkommen", "")
    erst = _lokal().get("/desktop/start", params={"code": GEHEIMNIS, "abgleich": "se"}).text
    assert 'location.replace("/?willkommen=1&abgleich=se")' in erst
    dann = _lokal().get("/desktop/start", params={"code": GEHEIMNIS, "abgleich": "se"}).text
    assert 'location.replace("/?abgleich=se")' in dann


def test_mit_dem_cloud_ordner_gewaehlt_geht_es_auch_zum_abgleich(desktop):
    """Seit 0.4.0 ist der Cloud-Ordner wählbar. Ohne Weitergabe landete man
    nach der Wahl im allgemeinen Assistenten statt bei der Einrichtung –
    und ein beliebiger Wert darf nicht in die Adresse durchrutschen."""
    import core
    core.set_setting("desktop_willkommen", "1")
    dann = _lokal().get("/desktop/start", params={"code": GEHEIMNIS, "abgleich": "wolke"}).text
    assert 'location.replace("/?abgleich=wolke")' in dann
    fremd = _lokal().get("/desktop/start", params={"code": GEHEIMNIS, "abgleich": "x\"y"}).text
    assert 'location.replace("/")' in fremd


def test_handerfassung_am_rechner_fragt_nur_den_katalog(desktop, monkeypatch):
    """Auf dem iPhone meldete die Handerfassung am 17.10.2026 sw0518 als
    „kennt BrickLink nicht“, obwohl der Katalog eingestellt war – die App
    hatte live bei BrickLink gefragt. Am Rechner ohne eigenen Schlüssel
    darf BrickLink gar nicht gefragt werden, und die Meldung nennt den
    Katalog."""
    import core
    import integrations
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)

    def nie(*a, **k):
        raise AssertionError("BrickLink gefragt")
    monkeypatch.setattr(integrations, "bricklink_item", nie)
    with _lokal() as c:
        token = re.search(r"'bf_token', \"([^\"]+)\"",
                          c.get("/desktop/start", params={"code": GEHEIMNIS}).text).group(1)
        kopf = {"Authorization": "Bearer " + token}
        with core.db() as conn:
            conn.execute("DELETE FROM katalog_index WHERE item_no IN ('sw0518', 'sw9999x')")
            conn.execute("INSERT INTO katalog_index (item_type, item_no, name, updated_at) "
                         "VALUES ('minifig', 'sw0518', 'Figur aus dem Katalog', 0)")
        r = c.get("/api/lookup/minifig/sw0518", headers=kopf)
        assert r.status_code == 200 and r.json()["quelle"] == "katalog"
        r = c.get("/api/lookup/minifig/sw9999x", headers=kopf)
        assert r.status_code == 404 and "Katalog" in r.json()["detail"]


def _js_funktion(name: str) -> str:
    js = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
    anfang = js.index(f"function {name}(")
    return js[anfang:js.index("\n}\n", anfang)]


def test_assistent_am_rechner_liegt_nicht_ueber_der_app():
    """Probelauf auf Windows 11 ARM (06.11.2026): Am Rechner ist die App beim
    Start schon sichtbar. Der Assistent lag darüber und blieb nach
    „Beitreten“ stehen, auch im Tab Sammlung. Der Assistent blendet die App
    aus, und ein eingerichteter Abgleich führt von selbst weiter."""
    assert '$("app").hidden = true' in _js_funktion("startWizard")
    js = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
    beitreten = js[js.index('$("btn-ordner-beitreten").addEventListener'):]
    beitreten = beitreten[:beitreten.index("\n  });\n")]
    assert "assistentWeiter()" in beitreten
    assert '$("wiz-next").click()' in _js_funktion("assistentWeiter")


@pytest.mark.skipif(not (REPO_WURZEL / "windows").exists(), reason="Das Windows-Programm liegt nur im Labor")
def test_live_scanner_ist_teil_des_fensters():
    """Im Probelauf vom 06.11.2026 ging das Fenstermenü nicht auf, der
    Live-Scanner war nicht zu erreichen; danach fragte er in eigenem Fenster
    nach SE-Zugangsdaten. 07.11.2026: „wie in der Mac-App … Komplett
    integriert.“ Der Scanner ist jetzt eine Karte im Tab Scannen, und das
    erkannte Bild geht durch denselben Weg wie ein hineingezogenes Foto."""
    wurzel = pathlib.Path(__file__).resolve().parents[1]
    html = (wurzel / "frontend" / "index.html").read_text(encoding="utf-8")
    scan = html[html.index('id="view-scan"'):]
    assert 'id="livescan-karte"' in scan
    assert 'id="btn-livescanner"' not in html
    assert "livescanLaden();" in _js_funktion("showApp")
    assert "handlePhoto(" in _js_funktion("livescanErkennen")
    start = (wurzel / "windows" / "nupplo_bruecke" / "__main__.py").read_text(encoding="utf-8")
    assert "def scanner(self):" not in start


def test_live_scanner_steht_links_unter_scannen():
    """07.11.2026, zur Karte im Tab Scannen: „Den hätte ich aber gern
    in der Menüleiste links, genau so wie in der Mac-App. Auch mit den Daten
    und so weiter.“ In der Mac-App steht „Live-Scanner“ gleich unter
    Scannen und öffnet den Scan-Bildschirm im Live-Modus: ohne Fotoknopf,
    mit Katalogbild neben der Aufnahme und einem Verlauf."""
    wurzel = pathlib.Path(__file__).resolve().parents[1]
    html = (wurzel / "frontend" / "index.html").read_text(encoding="utf-8")
    leiste = html[html.index('<nav class="tabbar">'):]
    leiste = leiste[:leiste.index("</nav>")]
    tabs = re.findall(r'data-tab="([a-z]+)"', leiste)
    assert tabs[:2] == ["scan", "live"]
    # Die Karte gehört in den Live-Modus, nicht mehr unter das Foto-Scannen.
    karte = re.search(r'<div id="livescan-karte" class="([^"]+)"', html).group(1)
    assert "nur-live" in karte.split()
    for teil in ('id="live-katalog"', 'id="live-verlauf"', 'id="livescan-nurfiguren"', 'id="livescan-ton"'):
        assert teil in html
    zeigen = _js_funktion("showTab")
    assert '"live-modus"' in zeigen
    # Ein leerer Abzug der Automatik ersetzt den Treffer nicht.
    assert "if (!items.length && automatisch && livescanAktiv) return;" in _js_funktion("livescanErkennen")
    assert "livescanModus()" in _js_funktion("livescanAbfragen")


def test_live_scanner_ansichten_zum_anhaken():
    """Nach dem Umzug in die Seitenleiste fehlte gegenüber der Mac-App noch
    die Wahl der Ansichten (08.11.2026: „mach bitte fertig“): Auf dem
    Drehteller sieht man dieselbe Figur von mehreren Seiten, und am Mac
    kommen die angehakten Ansichten als Fotos an den Artikel. Höchstens
    sechs, die älteste nicht angehakte fliegt zuerst."""
    wurzel = pathlib.Path(__file__).resolve().parents[1]
    html = (wurzel / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'id="live-ansichten"' in html
    dazu = _js_funktion("livescanAnsichtDazu")
    assert "!e.an.has(i)" in dazu and "LIVESCAN_ANSICHTEN_MAX" in dazu
    assert "livescanAnsichtenAnhaengen(it)" in _js_funktion("eigenbildAnhaengen")
    assert "v.an" in _js_funktion("livescanAnsichtenAnhaengen")
