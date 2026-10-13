"""Der Figurenkatalog kommt verschlüsselt (3.8.2).

Anlass: Am 13.10.2026 fiel auf, dass die Figurbeschreibungen im Klartext im
öffentlichen Repo stehen – unter MIT, also für jeden frei nehmbar. Sie
sollen nur in Nupplo genutzt werden, ohne Anmeldung und ohne Hub. Der
Katalogdienst schreibt deshalb `katalog/index.nupplo`; an der alten Stelle
steht ein Hinweis, der ältere Fassungen um ein Update bitten lässt.
"""
import base64
import json
import pathlib
import time

import pytest
import requests

import core
import katalogschutz
import main

PRUEFWERTE = json.loads((pathlib.Path(__file__).parent / "daten"
                         / "katalog-pruefwerte.json").read_text("utf-8"))


@pytest.fixture
def instanz(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "kg.db"))
    core.init_db()
    return tmp_path


def _datei(monkeypatch, inhalt, etag='"neu"'):
    geholt = []

    class Fake:
        status_code = 200
        headers = {"ETag": etag}
        content = inhalt.encode()
        text = inhalt

        def raise_for_status(self):
            pass

    def holen(url, **kw):
        geholt.append(url)
        return Fake()
    monkeypatch.setattr(main.requests, "get", holen)
    return geholt


def _zeile(text, falle=False):
    """Wie der Katalogdienst verschlüsselt – nachgebaut nur für den Test."""
    import hashlib
    import hmac
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    roh = text.encode("utf-8")
    nonce = hmac.new(katalogschutz._NONCE,
                     (b"falle" if falle else b"zeile") + b"\0" + roh,
                     hashlib.sha256).digest()[:12]
    schl = katalogschutz._ableiten(b"aes")
    return base64.b64encode(
        nonce + AESGCM(schl).encrypt(nonce, roh, katalogschutz.ZUSATZ)
    ).decode()


def test_pruefwerte_des_katalogdienstes():
    """Der Katalogdienst schreibt, Nupplo liest – beide gegen dieselben
    Prüfwerte. Stimmt hier etwas nicht, fände keine Installation mehr eine
    Figur, und niemand sähe einen Fehler."""
    for w in PRUEFWERTE["zeilen"]:
        erwartet = None if w["falle"] else w["text"]
        assert katalogschutz.zeile_lesen(w["zeile"]) == erwartet
    assert katalogschutz.zeile_lesen(PRUEFWERTE["fremd"]) is None
    assert base64.b64decode(PRUEFWERTE["schluessel"]) == \
        katalogschutz.SCHLUESSEL


def test_verschluesselter_katalog_kommt_an_fallen_nicht(instanz, monkeypatch):
    """Die Suche braucht die Beschreibungen weiter – aus der neuen Datei.
    Die Fallen dagegen dürfen nie in einer Installation landen: Sie wären
    Figuren, die es nicht gibt, und die Namenssuche fragte BrickLink
    vergeblich nach ihnen."""
    figur = {"item_no": "cas001", "art": "knight", "farben": "red, blue",
             "merkmale": "cape yellow green dragon", "modell": "geprüft"}
    falle = {"item_no": "cas999k", "farben": "white",
             "merkmale": "head yellow; torso white"}
    inhalt = "\n".join([
        "# Nupplo-Figurenkatalog – nur in Nupplo",
        _zeile(json.dumps({"hinweis": "Nur in Nupplo"})),
        _zeile(json.dumps(figur)),
        _zeile(json.dumps(falle), falle=True),
        "kaputt",
    ]) + "\n"
    geholt = _datei(monkeypatch, inhalt)
    assert main._katalog_ziehen()["neu"] == 1
    assert geholt == [main.KATALOG_QUELLE]
    assert geholt[0].endswith("/katalog/index.nupplo")
    with core.db() as conn:
        zeilen = [dict(r) for r in conn.execute(
            "SELECT item_no, merkmale FROM katalog_index")]
    assert [z["item_no"] for z in zeilen] == ["cas001"]
    assert zeilen[0]["merkmale"].startswith("cape yellow green dragon")


def test_hinweis_der_alten_datei_verschwindet_mit_dem_update(instanz,
                                                             monkeypatch):
    """Wer die alte Datei mit dem Hinweis schon geholt hat und danach
    aktualisiert, soll den Hinweis nicht weiter in der Suche haben – und
    seine Beschreibungen zurückbekommen, eingedeutscht wie vorher, sonst
    fände „gelber Kopf“ danach nichts mehr."""
    alt = [{"item_no": "cas001", "farben": "red",
            "merkmale": "bitte nupplo aktualisieren; please update nupplo"},
           {"item_no": "Bitte aktualisiere Nupplo: Der Figurenkatalog …",
            "name": "Bitte aktualisiere Nupplo", "farben": "kopf gelber",
            "merkmale": "–"}]
    _datei(monkeypatch, "\n".join(json.dumps(z) for z in alt) + "\n",
           etag='"alt"')
    core.set_setting("katalog_quelle", main.KATALOG_QUELLE_ALT)
    main._katalog_ziehen()
    with core.db() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM katalog_index"
                            ).fetchone()["c"] == 2

    figur = {"item_no": "cas001", "farben": "yellow, red",
             "merkmale": "head yellow smile; torso red armor"}
    geholt = _datei(monkeypatch, _zeile(json.dumps(figur)) + "\n")
    main._katalog_ziehen()
    # Die von Hand eingetragene alte Vorgabe zeigt jetzt auf die neue Datei.
    assert geholt == [main.KATALOG_QUELLE]
    with core.db() as conn:
        zeilen = [dict(r) for r in conn.execute(
            "SELECT item_no, merkmale FROM katalog_index")]
    assert [z["item_no"] for z in zeilen] == ["cas001"]
    assert "kopf" in zeilen[0]["merkmale"]


def test_eigene_quelle_im_klartext_geht_weiter(instanz, monkeypatch):
    """Wer `katalog_quelle` auf den eigenen Katalogdienst im Heimnetz
    stellt, bekommt dort Klartext. Das soll nicht daran scheitern, dass
    die öffentliche Datei jetzt verschlüsselt ist."""
    core.set_setting("katalog_quelle", "http://192.0.2.1:8320/index.ndjson")
    geholt = _datei(monkeypatch, json.dumps(
        {"item_no": "sw0001", "merkmale": "head yellow"}) + "\n")
    assert main._katalog_ziehen()["neu"] == 1
    assert geholt == ["http://192.0.2.1:8320/index.ndjson"]

