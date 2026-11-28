"""Abgleich über einen eigenen Cloud-Ordner (Nupplo für Windows 0.4.0).

Anlass: Sven hat am 16.10.2026 entschieden, dass Geräte ohne SE sich nur
über einen frei gewählten eigenen Cloud-Ordner verbinden, und am 06.11.2026,
dass Windows damit weitergeht, bevor Android beginnt. Mehrere Geräte – auch
zweier Personen – schreiben dabei in denselben Ordner, und der
Cloud-Dienst liefert die Dateien irgendwann und in beliebiger Reihenfolge.

Geprüft wird mit mehreren echten Gerätedatenbanken und einem echten
Ordner. Was dabei schiefgehen kann, ohne dass man es gleich sieht:
- zwei Geräte sehen nach dem Abgleich verschiedene Sammlungen, weil die
  Pakete in anderer Reihenfolge ankamen;
- dieselbe Figur, offline auf zwei Geräten erfasst, steht doppelt da – oder
  ihre Menge kommt auf jedem Gerät, das das Paar bemerkt, noch einmal dazu;
- ein halb übertragenes Paket wird übersprungen und ist für immer verloren;
- der Cloud-Dienst kann die Sammlung lesen.
"""
import contextlib
import pathlib
import time

import pytest

import main  # noqa: F401  – bindet sync, abgleich und ordner
import core  # noqa: E402
import desktop  # noqa: E402
import ordner  # noqa: E402


class Geraet:
    def __init__(self, tmp, name):
        self.db = str(tmp / f"{name}.db")
        self.name = name
        with self.aktiv():
            core.init_db()
            desktop.konto()

    @contextlib.contextmanager
    def aktiv(self):
        alt = core.DB_PATH
        core.DB_PATH = self.db
        try:
            yield
        finally:
            core.DB_PATH = alt

    def anlegen(self, nummer, menge=1, zustand="used", uuid=None):
        with self.aktiv(), core.db() as conn:
            conn.execute("INSERT INTO collection (item_id, item_type, name, quantity, condition, "
                         "added_at, uuid) VALUES (?, 'minifig', ?, ?, ?, ?, ?)",
                         (nummer, "Figur " + nummer, menge, zustand, int(time.time()), uuid))
            return conn.execute("SELECT id FROM collection WHERE item_id = ? AND condition = ?",
                                (nummer, zustand)).fetchone()[0]

    def kauf(self, eintrag):
        with self.aktiv(), core.db() as conn:
            conn.execute("INSERT INTO purchases (entry_id, quantity, unit_price, created_at) "
                         "VALUES (?, 1, 5, ?)", (eintrag, int(time.time())))

    def pakete(self):
        with self.aktiv():
            ich = core.get_setting("ordner_geraet")
            return sorted(p.name for p in pathlib.Path(core.get_setting("ordner_pfad"))
                          .joinpath("Nupplo-Abgleich", "geraete", ich).glob("*.paket"))

    def aendern(self, nummer, **werte):
        with self.aktiv(), core.db() as conn:
            spalten = ", ".join(f"{k} = ?" for k in werte)
            conn.execute(f"UPDATE collection SET {spalten} WHERE item_id = ?",
                         (*werte.values(), nummer))

    def loeschen(self, nummer):
        with self.aktiv(), core.db() as conn:
            conn.execute("DELETE FROM collection WHERE item_id = ?", (nummer,))

    def sammlung(self):
        with self.aktiv(), core.db() as conn:
            return sorted((r["item_id"], r["condition"], r["quantity"], r["notes"] or None) for r in
                          conn.execute("SELECT * FROM collection"))

    def abgleichen(self):
        with self.aktiv():
            return ordner.abgleichen()

    def einrichten(self, pfad, code="", ersetzen=False):
        with self.aktiv():
            return ordner.einrichten(str(pfad), code, ersetzen)

    def code(self):
        with self.aktiv():
            import base64
            return ordner.code_aus(base64.b64decode(core.get_setting("ordner_schluessel")))


def _entpackt(daten):
    import gzip
    try:
        return gzip.decompress(daten)
    except Exception:
        return b""


@pytest.fixture
def wolke(tmp_path):
    p = tmp_path / "OneDrive"
    p.mkdir()
    return p


@pytest.fixture
def geraete(tmp_path):
    return [Geraet(tmp_path, n) for n in ("a", "b", "c")]


def _alle(geraete, runden=2):
    for _ in range(runden):
        for g in geraete:
            g.abgleichen()


def _verbunden(wolke, geraete):
    a, *rest = geraete
    a.einrichten(wolke)
    for g in rest:
        g.einrichten(wolke, a.code())
    return geraete


def test_was_ein_geraet_anlegt_kommt_bei_allen_an(wolke, geraete):
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0001", 2)
    _alle(geraete)
    assert b.sammlung() == c.sammlung() == [("sw0001", "used", 2, None)]


def test_der_cloud_dienst_liest_nichts_mit(wolke, geraete):
    """Die Sammlung verlässt das Gerät nicht lesbar – auch nicht in den
    eigenen Cloud-Ordner, den der Anbieter durchsuchen kann."""
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0815", 1)
    a.aendern("sw0815", notes="Geheimfach")
    a.abgleichen()
    for f in wolke.rglob("*"):
        if not f.is_file():
            continue
        roh = f.read_bytes()
        # Auch hinter gzip nachsehen: Gepackt sieht Klartext nur verschlüsselt aus.
        lesbar = [roh] + [_entpackt(roh[i:]) for i in range(len(roh)) if roh[i:i + 2] == b"\x1f\x8b"]
        assert not any(b"sw0815" in x or b"Geheimfach" in x for x in lesbar), f.name


def test_loeschen_kommt_an(wolke, geraete):
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0001")
    _alle(geraete)
    b.loeschen("sw0001")
    _alle(geraete)
    assert a.sammlung() == c.sammlung() == []


def test_neueste_aenderung_gewinnt_egal_in_welcher_reihenfolge(wolke, geraete):
    """B und C ändern dieselbe Figur, ohne voneinander zu wissen. A liest
    die Pakete in einer Reihenfolge, B und C jeweils das andere zuletzt –
    am Ende muss überall dasselbe stehen."""
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0001")
    _alle(geraete)
    b.aendern("sw0001", notes="von B", updated_at=int(time.time() * 1000) + 1000)
    c.aendern("sw0001", notes="von C", updated_at=int(time.time() * 1000) + 2000)
    b.abgleichen()
    c.abgleichen()
    _alle([c, b, a])
    assert a.sammlung() == b.sammlung() == c.sammlung() == [("sw0001", "used", 1, "von C")]


def test_dieselbe_figur_auf_zwei_geraeten_wird_einmal_zusammengelegt(wolke, geraete):
    """A und B erfassen dieselbe Figur, bevor sie voneinander wissen. Danach
    soll sie überall einmal stehen, mit beiden Mengen zusammen – und nicht
    mit der Summe mal der Zahl der Geräte, die das Paar bemerken."""
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0002", 1)
    b.anlegen("sw0002", 2)
    a.abgleichen()
    b.abgleichen()
    _alle(geraete, runden=3)
    assert a.sammlung() == b.sammlung() == c.sammlung() == [("sw0002", "used", 3, None)]


def test_beitritt_mit_eigener_sammlung_legt_zusammen(wolke, geraete):
    """Wer schon Figuren hat und beitritt, verliert sie nicht."""
    a, b, c = geraete
    a.anlegen("sw0001")
    a.einrichten(wolke)
    b.anlegen("sw0003", 4)
    b.einrichten(wolke, a.code())
    _alle([a, b])
    assert a.sammlung() == b.sammlung() == [("sw0001", "used", 1, None),
                                            ("sw0003", "used", 4, None)]


def test_ersetzen_loescht_im_ordner_nichts(wolke, geraete):
    """„Durch den Ordner ersetzen“ räumt nur hier auf. Ginge das Aufräumen
    als Löschung hinaus, verschwände die Sammlung auf allen Geräten."""
    a, b, c = geraete
    a.anlegen("sw0001")
    a.einrichten(wolke)
    b.anlegen("sw0001")
    b.anlegen("sw0009")
    b.einrichten(wolke, a.code(), ersetzen=True)
    _alle([a, b])
    assert a.sammlung() == b.sammlung() == [("sw0001", "used", 1, None)]


def test_falscher_code_kommt_nicht_hinein(wolke, geraete):
    a, b, c = geraete
    a.einrichten(wolke)
    with pytest.raises(ordner.OrdnerFehler):
        b.einrichten(wolke, ordner.code_aus(b"\x01" * 32))


def test_halb_uebertragenes_paket_geht_nicht_verloren(wolke, geraete):
    """Cloud-Dienste legen Dateien an, bevor sie ganz da sind. Ein
    unlesbares Paket darf nicht als gelesen gelten."""
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0001")
    a.abgleichen()
    paket = max((wolke / "Nupplo-Abgleich" / "geraete").rglob("*.paket"),
                key=lambda f: f.stat().st_mtime_ns)
    ganz = paket.read_bytes()
    paket.write_bytes(ganz[:len(ganz) // 2])
    b.abgleichen()
    assert b.sammlung() == []
    paket.write_bytes(ganz)
    b.abgleichen()
    assert b.sammlung() == [("sw0001", "used", 1, None)]


def test_ruhig_schreibt_keine_neuen_pakete(wolke, geraete):
    """Was ein Gerät nur gelesen hat, schickt es nicht zurück. Sonst schriebe
    jeder Blick in den Ordner ein neues Paket, und die Geräte schaukelten
    sich gegenseitig auf."""
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0001")
    _alle(geraete)
    vorher = sorted(p.name for p in wolke.rglob("*.paket"))
    _alle(geraete)
    assert sorted(p.name for p in wolke.rglob("*.paket")) == vorher


def test_code_ist_derselbe_schluessel():
    import secrets
    s = secrets.token_bytes(32)
    code = ordner.code_aus(s)
    assert ordner.schluessel_aus(code.lower().replace("-", " ")) == s


def test_wer_das_paar_spaeter_bemerkt_zaehlt_nicht_noch_einmal_dazu(wolke, geraete):
    """C hat Bs Figur schon, als B sie mit As zusammenlegt. Legte C auch
    zusammen, stünde dort 3 + 2 = 5. Feste UUIDs, damit es jedes Mal
    genau diesen Weg nimmt (die kleinere UUID bleibt)."""
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0002", 1, uuid="11111111-1111-4111-8111-111111111111")
    b.anlegen("sw0002", 2, uuid="22222222-2222-4222-8222-222222222222")
    b.abgleichen()
    c.abgleichen()
    assert c.sammlung() == [("sw0002", "used", 2, None)]
    a.abgleichen()
    b.abgleichen()
    c.abgleichen()
    _alle(geraete)
    assert a.sammlung() == b.sammlung() == c.sammlung() == [("sw0002", "used", 3, None)]


def test_spaeteres_paket_zuerst_da_frueheres_geht_nicht_verloren(wolke, geraete):
    """Der Cloud-Dienst liefert Paket 2 vor Paket 1. Wer 2 schon als gelesen
    merkt, überspringt 1 für immer."""
    a, b, c = _verbunden(wolke, geraete)
    a.anlegen("sw0001")
    a.abgleichen()
    a.anlegen("sw0002")
    a.abgleichen()
    eigen = wolke / "Nupplo-Abgleich" / "geraete"
    erstes = sorted(eigen.rglob("*.paket"))[0]
    # Das Beitrittspaket von A ist leer; das mit sw0001 ist das zweite.
    pakete = sorted(p for p in eigen.rglob("*.paket") if p.parent == erstes.parent)
    weg = pakete[-2]
    versteck = weg.with_suffix(".unterwegs")
    weg.rename(versteck)
    b.abgleichen()
    versteck.rename(weg)
    b.abgleichen()
    assert b.sammlung() == [("sw0001", "used", 1, None), ("sw0002", "used", 1, None)]


def test_kinder_einer_loeschung_gehen_nicht_zurueck(wolke, geraete):
    """Löscht A eine Figur mit Kaufposten, löscht B die Posten beim
    Übernehmen mit. Das ist Echo – B soll dafür kein eigenes Paket
    schreiben."""
    a, b, c = _verbunden(wolke, geraete)
    a.kauf(a.anlegen("sw0001"))
    _alle(geraete)
    with a.aktiv(), core.db() as conn:          # wie die Web-App: Posten mit
        conn.execute("DELETE FROM purchases")
    a.loeschen("sw0001")
    a.abgleichen()
    vorher = b.pakete()
    b.abgleichen()
    assert b.sammlung() == []
    assert b.pakete() == vorher


def test_nachgezogene_summe_ist_echo(wolke, geraete):
    """A löscht einen von zwei Kaufposten; der Eintrag bekommt eine neue
    Summe, und beides geht in den Ordner. B zieht beim Übernehmen dieselbe
    Summe noch einmal nach – das ist kein Grund für ein eigenes Paket."""
    import sync
    a, b, c = _verbunden(wolke, geraete)
    eintrag = a.anlegen("sw0001", 2)
    a.kauf(eintrag)
    a.kauf(eintrag)
    _alle(geraete)
    with a.aktiv(), core.db() as conn:
        posten = conn.execute("SELECT * FROM purchases LIMIT 1").fetchone()
        sync._loeschen(conn, "purchases", posten, int(time.time() * 1000))
    a.abgleichen()
    vorher = b.pakete()
    b.abgleichen()
    b.abgleichen()
    assert b.pakete() == vorher


def test_einrichten_ueber_die_oberflaeche(wolke, tmp_path, monkeypatch):
    """Der Weg, den die Abgleich-Karte geht: prüfen, anlegen, Code holen.
    Und solange der Ordner-Abgleich läuft, kein zweiter mit der SE – zwei
    Abgleiche zugleich schickten jede Änderung auf zwei Wegen hinaus."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    g = Geraet(tmp_path, "ui")
    with g.aktiv():
        uid, name = desktop.konto()
        c = TestClient(main.app)
        c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
        r = c.get("/api/ordner/pruefen", params={"pfad": str(wolke)})
        assert r.json() == {"vorhanden": False, "geraete": 0, "hier_leer": True}
        assert c.post("/api/ordner/einrichten", json={"pfad": str(wolke)}).status_code == 200
        stand = c.get("/api/ordner").json()
        # Auch mit leerer Sammlung zählt das Gerät schon mit.
        assert stand["an"] is True and stand["geraete"] == 1
        code = c.get("/api/ordner/code").json()["code"]
        assert len(code.replace("-", "")) == 52
        r = c.post("/api/abgleich/verbinden", json={"adresse": "http://127.0.0.1:9",
                                                    "benutzer": "x", "passwort": "y"})
        assert r.status_code == 400 and "Cloud-Ordner" in r.json()["detail"]


def test_qr_code_traegt_code_und_ordner(wolke, tmp_path, monkeypatch):
    """52 Zeichen tippt auf dem Telefon niemand fehlerfrei ab. Der QR-Code
    muss genau den Schlüssel tragen, der den Ordner öffnet, und die Kennung
    des Ordners, damit das Telefon einen falsch gewählten Ordner erkennt."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    g = Geraet(tmp_path, "qr")
    g.einrichten(wolke)
    with g.aktiv():
        uid, name = desktop.konto()
        c = TestClient(main.app)
        c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
        r = c.get("/api/ordner/qr")
        assert r.status_code == 200 and r.text.lstrip().startswith(("<?xml", "<svg"))
        art, code, kennung = ordner.qr_inhalt(g.code(), core.get_setting("ordner_id")).split(":")
        assert art == "NUPPLO-ORDNER"
        kopf = ordner.ordner_lesen(ordner.wurzel(str(wolke)))
        assert kennung.lower() == kopf["id"]
        assert ordner._pruefwert(ordner.schluessel_aus(code), kopf["id"]) == kopf["pruef"]
