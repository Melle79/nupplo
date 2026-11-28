"""Abgleich über Google Drive (Nupplo für Windows 0.8.0).

Anlass: Sven hat am 25.11.2026 entschieden, dass Geräte ohne SE über
Google Drive abgleichen – „wenn jemand ein Android-Gerät hat, hat er auch
Google Drive“ –, ohne dass Nupplo die Sammlungen auf eigenen Servern
speichert. Telefone haben keinen Drive-Ordner auf der Platte, also spricht
auch Windows mit der Drive-Schnittstelle, und zwar nur mit `drive.file`.

Drive ist keine Platte. Was dabei schiefgehen kann:
- Zwei Geräte richten gleichzeitig ein. Drive kennt kein „nur anlegen,
  wenn es das noch nicht gibt“, also entstehen zwei `ordner.json` mit zwei
  Schlüsseln, und die Geräte reden aneinander vorbei.
- Zwei Ordner „Nupplo-Abgleich“ – jedes Gerät nimmt einen anderen.
- Drive fällt mitten im Lauf aus, und ein Paket gilt als geschickt, obwohl
  es nie ankam.
- Google kann die Sammlung lesen.
"""
import json

import pytest

import main  # noqa: F401  – bindet sync, abgleich und ordner
import ablage  # noqa: E402
import google_konto  # noqa: E402
import ordner  # noqa: E402
from nachgebautes_drive import ORDNER, NachgebautesDrive  # noqa: E402
from test_ordner import Geraet, _alle, _entpackt  # noqa: E402


@pytest.fixture
def drive(monkeypatch):
    d = NachgebautesDrive()
    monkeypatch.setattr(google_konto, "verbunden", lambda: True)
    monkeypatch.setattr(google_konto, "sitzung", lambda: d)
    return d


@pytest.fixture
def geraete(tmp_path):
    return [Geraet(tmp_path, n) for n in ("a", "b", "c")]


def _verbunden(geraete):
    a, *rest = geraete
    a.einrichten(ordner.GOOGLE)
    for g in rest:
        g.einrichten(ordner.GOOGLE, a.code())
    return geraete


def test_was_ein_geraet_anlegt_kommt_ueber_drive_bei_allen_an(drive, geraete):
    a, b, c = _verbunden(geraete)
    a.anlegen("sw0001", 2)
    b.anlegen("sw0002", 1)
    _alle(geraete)
    assert a.sammlung() == b.sammlung() == c.sammlung() == [
        ("sw0001", "used", 2, None), ("sw0002", "used", 1, None)]
    # Aufbau wie auf der Platte, damit Telefone dasselbe lesen
    assert drive.pfad("Nupplo-Abgleich", "ordner.json")
    assert drive.pfad("Nupplo-Abgleich", "geraete")


def test_google_liest_nichts_mit(drive, geraete):
    a, b, c = _verbunden(geraete)
    a.anlegen("sw0815", 1)
    a.aendern("sw0815", notes="Geheimfach")
    a.abgleichen()
    for roh in drive.inhalte():
        lesbar = [roh] + [_entpackt(roh[i:]) for i in range(len(roh)) if roh[i:i + 2] == b"\x1f\x8b"]
        assert not any(b"sw0815" in x or b"Geheimfach" in x for x in lesbar)


def test_gleichzeitig_eingerichtet_gilt_ein_schluessel(drive, geraete):
    """A und B richten im selben Augenblick ein, keiner sieht den anderen
    vorher. Danach darf es nur einen Abgleich geben: B muss erfahren, dass
    A schneller war, und sein eigenes ordner.json wieder wegnehmen."""
    a, b, _ = geraete
    a.einrichten(ordner.GOOGLE)
    kopf_a = drive.dateien[drive.pfad("Nupplo-Abgleich", "ordner.json")]
    # B hat nachgesehen, als es noch keinen gab, und lädt jetzt hoch.
    w = ablage.GoogleDrive(drive)
    zweiter = {"format": 1, "id": "ffffffffffffffff", "erstellt": 0, "pruef": "x"}
    kopf_a["trashed"] = True
    drive.vor_hochladen = lambda meta: kopf_a.update(trashed=False)
    assert w.kopf_anlegen(json.dumps(zweiter).encode()) is False
    koepfe = [f for f in drive.dateien.values() if f["name"] == "ordner.json"]
    assert len(koepfe) == 1 and koepfe[0]["id"] == kopf_a["id"]
    with pytest.raises(ordner.OrdnerFehler, match="schon einen Abgleich"):
        b.einrichten(ordner.GOOGLE)


def test_zwei_abgleichsordner_alle_nehmen_den_aelteren(drive, geraete):
    """Haben zwei Geräte gleichzeitig „Nupplo-Abgleich“ angelegt, gibt es den
    Ordner zweimal. Jedes Gerät muss denselben nehmen – den älteren –,
    egal in welcher Reihenfolge Drive sie auflistet."""
    a, b, c = geraete
    alt = drive.anlegen("Nupplo-Abgleich", "root", ORDNER)
    drive.anlegen("Nupplo-Abgleich", "root", ORDNER)
    _verbunden(geraete)
    a.anlegen("sw0001")
    _alle(geraete)
    assert c.sammlung() == [("sw0001", "used", 1, None)]
    koepfe = [f for f in drive.dateien.values() if f["name"] == "ordner.json"]
    assert [k["parents"][0] for k in koepfe] == [alt]


def test_ausfall_mitten_im_lauf_verliert_nichts(drive, geraete):
    """Das Hochladen scheitert (Netz weg, Drive 503). Der Lauf meldet einen
    Fehler, und das Paket geht beim nächsten Mal hinaus, statt als
    geschickt zu gelten."""
    a, b, _ = geraete
    a.einrichten(ordner.GOOGLE)
    b.einrichten(ordner.GOOGLE, a.code())
    a.anlegen("sw0007", 3)
    drive.stoerung.append(("POST", "/upload/", 503))
    with pytest.raises(ordner.OrdnerFehler, match="503"):
        a.abgleichen()
    a.abgleichen()
    b.abgleichen()
    assert b.sammlung() == [("sw0007", "used", 3, None)]


def test_beitreten_mit_falschem_code_scheitert(drive, geraete):
    a, b, _ = geraete
    a.einrichten(ordner.GOOGLE)
    falsch = ordner.code_aus(b"\x01" * 32)
    with pytest.raises(ordner.OrdnerFehler, match="Code passt nicht"):
        b.einrichten(ordner.GOOGLE, falsch)


def test_fremde_dateien_im_ordner_stoeren_nicht(drive, geraete):
    """Mit drive.file sieht Nupplo nicht, was andere Programme in den Ordner
    legen – etwa eine Kopie von Hand. Der Abgleich darf daran nicht hängen."""
    a, b, _ = _verbunden(geraete)
    geraete_ordner = drive.pfad("Nupplo-Abgleich", "geraete")
    drive.anlegen("0123456789abcdef", geraete_ordner, ORDNER, app="andere")
    a.anlegen("sw0001")
    _alle([a, b])
    assert b.sammlung() == [("sw0001", "used", 1, None)]
    with b.aktiv():
        assert ordner.geraete_im_ordner(ordner.wurzel(ordner.GOOGLE)) == 3


def test_anmeldung_nur_mit_eigenem_state_und_drive_berechtigung(monkeypatch, tmp_path):
    """Der Rückweg von Google ist ohne Nupplo-Anmeldung erreichbar (es ist
    der Browser, nicht die App). Ein fremder Aufruf ohne den `state` dieser
    Anmeldung darf keinen Zugang hinterlegen, und wer bei Google den
    Drive-Zugriff abwählt, soll es gleich erfahren statt beim ersten Abgleich."""
    import urllib.parse

    from test_ordner import Geraet
    g = Geraet(tmp_path, "g")
    monkeypatch.setenv("NUPPLO_GOOGLE_CLIENT_ID", "kennung")
    monkeypatch.setenv("NUPPLO_GOOGLE_CLIENT_SECRET", "geheim")
    gesendet = []

    class Antwort:
        status_code = 200

        def __init__(self, daten):
            self._d = daten

        def json(self):
            return self._d

    scope = {"wert": google_konto.BERECHTIGUNG}

    def post(url, data=None, timeout=None):
        gesendet.append(data)
        return Antwort({"access_token": "a", "refresh_token": "r", "expires_in": 3600,
                        "scope": scope["wert"]})

    import requests
    monkeypatch.setattr(requests, "post", post)
    with g.aktiv():
        adresse = google_konto.anmelde_adresse("http://127.0.0.1:47821/api/ordner/google/zurueck")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(adresse).query))
        assert q["scope"] == google_konto.BERECHTIGUNG and q["code_challenge_method"] == "S256"
        with pytest.raises(ablage.AblageFehler):
            google_konto.zurueck("fremd", "code")
        assert not google_konto.verbunden() and gesendet == []
        scope["wert"] = "openid"
        with pytest.raises(ablage.AblageFehler, match="Ohne Zugriff"):
            google_konto.zurueck(q["state"], "code")
        assert not google_konto.verbunden()
        scope["wert"] = google_konto.BERECHTIGUNG
        adresse = google_konto.anmelde_adresse("http://127.0.0.1:47821/api/ordner/google/zurueck")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(adresse).query))
        google_konto.zurueck(q["state"], "code")
        assert google_konto.verbunden()
        assert gesendet[-1]["code_verifier"] and gesendet[-1]["redirect_uri"].endswith("/zurueck")
        # Derselbe state ein zweites Mal geht nicht.
        with pytest.raises(ablage.AblageFehler):
            google_konto.zurueck(q["state"], "code")
