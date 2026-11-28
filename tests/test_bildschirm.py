"""Live-Scanner im Fenster der App (Windows 0.5.0, `backend/bildschirm.py`).

Anlass: 07.11.2026, zum bisherigen Live-Scanner in eigenem Fenster,
der nach SE-Zugangsdaten fragte: „mir gefällt es am besten, wenn es wie in
der Mac-App wäre. Komplett integriert.“ Der Wächter zog dafür aus dem
Tk-Scanner in die SE um. Seine Regeln stammen aus vielen Abenden mit echten
Streams; die Tests halten fest, dass sie den Umzug überstanden haben.
"""
import sys

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main  # zuerst: abgleich und main brauchen einander beim Laden

import bildschirm
import core
import desktop

STILL = [100] * (bildschirm.FINGER_KANTE ** 2)


def _anders(wert):
    return [wert] * (bildschirm.FINGER_KANTE ** 2)


def _takte(w, abdruecke, start=0.0):
    """Gibt zurück, in welchen Takten ausgelöst wurde."""
    return [i for i, a in enumerate(abdruecke) if w.takt(a, start + i * bildschirm.TAKT)]


def test_was_beim_einschalten_still_steht_wird_einmal_erkannt():
    """Wer einschaltet, während eine Figur hochgehalten wird, will diese
    Figur – aber nur einmal, nicht bei jedem weiteren Takt Stillstand."""
    w = bildschirm.Waechter("mittel")
    assert _takte(w, [STILL] * 12) == [bildschirm.RUHE]


def test_eine_neue_figur_loest_wieder_aus():
    w = bildschirm.Waechter("mittel")
    folge = [STILL] * 8 + [_anders(160)] * 8
    ausgeloest = _takte(w, folge, start=0.0)
    assert ausgeloest == [bildschirm.RUHE, 8 + bildschirm.RUHE]


def test_dasselbe_bild_nach_einem_wackler_wird_nicht_noch_einmal_geschickt():
    """Jemand greift kurz ins Bild, danach steht dieselbe Figur wieder da.
    Ohne die Regel ginge dasselbe Bild ein zweites Mal zur Erkennung."""
    w = bildschirm.Waechter("mittel")
    folge = [STILL] * 8 + [_anders(200)] + [STILL] * 8
    assert _takte(w, folge) == [bildschirm.RUHE]


def test_drehscheibe_loest_auch_ohne_stillstand_aus():
    """Manche Verkäufer stellen die Figur auf einen Drehteller – das Bild
    wird nie still. Dann wird mittendrin geschickt, aber begrenzt."""
    w = bildschirm.Waechter("mittel")
    # Jeder Takt etwas anders (über „still“, unter „Sprung“), und weil sich
    # die Figur dreht, sieht jede Ansicht anders aus als die zuletzt
    # geschickte. Ohne Obergrenze ginge sie ein Dutzend Mal pro Minute raus.
    folge = [_anders(100 + i * 3) for i in range(80)]
    ausgeloest = _takte(w, folge)
    assert len(ausgeloest) == bildschirm.ANSICHTEN


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    monkeypatch.setattr(bildschirm, "verfuegbar", lambda: True)
    aufnahmen = []

    def greifen(bereich=None):
        aufnahmen.append(bereich)
        return Image.new("RGB", (3200, 1800) if bereich is None else bereich[2:], (90, 90, 90))

    monkeypatch.setattr(bildschirm, "_greifen", greifen)
    core.set_setting("bildschirm_bereich", "")   # die Test-Datenbank bleibt über Läufe liegen
    uid, name = desktop.konto()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
    c.aufnahmen = aufnahmen
    yield c
    bildschirm.anhalten()


def test_bereich_waehlen_und_jetzt_erkennen(client):
    """Der Weg durch die Karte im Tab Scannen: Foto des Bildschirms holen,
    Bereich aufziehen, einmal erkennen lassen. Das Foto kommt verkleinert,
    der Maßstab steht dabei, damit die App zurückrechnen kann."""
    foto = client.get("/api/bildschirm/foto")
    assert foto.status_code == 200 and foto.headers["content-type"] == "image/png"
    assert float(foto.headers["X-Massstab"]) == pytest.approx(0.5)
    assert client.post("/api/bildschirm/jetzt").status_code == 400   # noch kein Bereich
    r = client.post("/api/bildschirm/bereich", json={"x": 100, "y": 50, "b": 640, "h": 360})
    assert r.json()["bereich"] == [100, 50, 640, 360]
    n = client.post("/api/bildschirm/jetzt").json()["nummer"]
    bild = client.get("/api/bildschirm/bild")
    assert bild.headers["X-Nummer"] == str(n)
    assert client.aufnahmen[-1] == (100, 50, 640, 360)


def test_eigenes_fenster_wird_nicht_mitfotografiert(client, monkeypatch):
    """Auf dem Mac lässt ScreenCaptureKit Nupplos Fenster weg. Hier blendet
    das Programm es für die Aufnahme aus – und danach wieder ein, sonst fehlte
    Nupplo auf jedem Bildschirmfoto, das man selbst macht."""
    gerufen = []
    monkeypatch.setattr(bildschirm, "FENSTER_AUSBLENDEN", gerufen.append)
    client.get("/api/bildschirm/foto")
    assert gerufen == [True, False]


def test_automatik_laeuft_im_hintergrund(client, monkeypatch):
    monkeypatch.setattr(bildschirm, "TAKT", 0.01)
    client.post("/api/bildschirm/bereich", json={"x": 0, "y": 0, "b": 64, "h": 64})
    s = client.post("/api/bildschirm/wachen", json={"an": True, "stufe": "hoch"}).json()
    assert s["an"] and s["stufe"] == "hoch"
    import time
    for _ in range(200):
        if client.get("/api/bildschirm").json()["nummer"] > 0:
            break
        time.sleep(0.01)
    assert client.get("/api/bildschirm").json()["nummer"] > 0
    assert client.post("/api/bildschirm/wachen", json={"an": False}).json()["an"] is False


def test_auf_dem_nas_gibt_es_keinen_live_scanner(monkeypatch):
    """Die SE auf dem NAS hat keinen Bildschirm; die Endpunkte gibt es dort
    nicht, und die Karte bleibt aus."""
    monkeypatch.delenv("NUPPLO_DESKTOP", raising=False)
    uid, name = desktop.konto()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
    assert c.get("/api/bildschirm").status_code == 404
    assert c.get("/api/bildschirm/foto").status_code == 404


@pytest.mark.skipif(sys.platform != "win32", reason="nur auf einem echten Windows (CI)")
def test_echter_bildschirm_und_bereich():
    """Die Aufnahme ist hier nur nachgebaut. Ob PIL auf Windows den ganzen
    Desktop greift und ein Bereich in Bildschirm-Koordinaten (Ursprung
    links oben auf dem virtuellen Bildschirm) genau so groß ankommt, wie er
    gewählt wurde, prüft der GitHub-Rechner."""
    ganz = bildschirm._greifen()
    assert ganz.width > 100 and ganz.height > 100
    x, y = bildschirm._ursprung()
    assert bildschirm._greifen((x, y, 64, 48)).size == (64, 48)
