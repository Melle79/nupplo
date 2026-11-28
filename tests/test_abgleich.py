"""Abgleich von Nupplo für Windows mit der SE zu Hause (16.10.2026).

Anlass: Sven hat entschieden, dass Windows wie iPhone und Mac immer eigene
Daten hat und sich wahlweise mit der SE abgleicht („Es sollen alle das
selbe können“). Geprüft wird gegen eine **echte** zweite SE aus diesem Repo
(eigener Prozess, eigene Datenbank), nicht gegen eine Annahme über sie.
Hier im Test ist die SE dieses Prozesses „der Rechner“.

Was dabei schiefgehen kann, ohne dass man es gleich sieht:
- ein Satz geht im Kreis (Echo), und jeder Abgleich schickt alles erneut;
- dieselbe Figur, offline auf beiden Seiten erfasst, steht danach doppelt
  da oder eine Seite verliert ihre Menge;
- eine Löschung kommt nicht an, und die Figur ist beim nächsten Mal zurück.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

import main  # noqa: F401  – bindet sync und abgleich in der richtigen Reihenfolge
import abgleich  # noqa: E402
import core  # noqa: E402
import desktop  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]


def _frei() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _json(methode, url, daten=None, token=None):
    a = urllib.request.Request(
        url, method=methode, data=json.dumps(daten).encode() if daten is not None else None,
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(a, timeout=20) as r:
        return json.loads(r.read())


class Zuhause:
    """Die SE zu Hause – echt, im eigenen Prozess."""

    def __init__(self, tmp):
        self.port = _frei()
        self.db = tmp / "zuhause.db"
        self.adresse = f"http://127.0.0.1:{self.port}"
        env = dict(os.environ, DB_PATH=str(tmp / "zuhause.db"),
                   SECRET_KEY_FILE=str(tmp / "secret"), FRONTEND_DIR=str(REPO / "frontend"))
        for weg in ("SECRET_KEY", "NUPPLO_DESKTOP", "NUPPLO_DESKTOP_GEHEIMNIS"):
            env.pop(weg, None)
        self.log = tmp / "zuhause.log"
        self.prozess = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
             "--port", str(self.port)], cwd=REPO / "backend", env=env,
            stdout=open(self.log, "wb"), stderr=subprocess.STDOUT)
        ende = time.time() + 60
        while True:
            try:
                _json("GET", self.adresse + "/api/setup")
                break
            except Exception:
                if time.time() > ende or self.prozess.poll() is not None:
                    raise RuntimeError(self.log.read_text(errors="replace")[-3000:])
                time.sleep(0.3)
        self.token = _json("POST", self.adresse + "/api/setup",
                           {"username": "zuhause", "password": "ein-langes-passwort"})["token"]

    def api(self, methode, weg, daten=None):
        return _json(methode, self.adresse + weg, daten, self.token)

    def figur(self, nummer):
        for z in self.api("GET", "/api/collection")["items"]:
            if z["item_id"] == nummer:
                return z
        return None

    def sql(self, befehl, *werte):
        """Direkt in die Datenbank zu Hause – Preise setzt dort sonst nur
        der Preisdienst. Die Trigger laufen trotzdem."""
        import sqlite3
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(befehl, werte)]

    def figuren(self, nummer):
        return [z for z in self.api("GET", "/api/collection")["items"] if z["item_id"] == nummer]


@pytest.fixture(autouse=True)
def rechner_db(monkeypatch, tmp_path):
    """Eigene Datenbank für den Rechner. Ohne sie liefen diese Tests gegen
    die gemeinsame Vorgabe-Datei aus conftest: Sie scheiterten, wenn die
    noch leer war, und legten sonst Tabellen an, an denen spätere Tests
    den Preisdienst statt BrickLink erkannten (test_price_cache)."""
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "rechner.db"))
    core.init_db()
    desktop.konto()          # wie `desktop.einrichten` beim Start


@pytest.fixture(scope="module")
def zuhause(tmp_path_factory):
    z = Zuhause(tmp_path_factory.mktemp("zuhause"))
    yield z
    z.prozess.terminate()
    z.prozess.wait(10)


@pytest.fixture
def gegen(zuhause, monkeypatch):
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    with core.db() as conn:
        abgleich.migrieren(conn)
        for t in ("collection", "purchases", "wanted", "shopping_items", "shopping_lists",
                  "abgleich_saetze", "abgleich_konflikte"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM settings WHERE name LIKE 'abgleich_%'")
    # Der Rechner holt erst einmal alles, was zu Hause schon liegt.
    g = abgleich.HttpGegenstelle(zuhause.adresse, zuhause.token)
    abgleich.abgleichen(g)
    return g


def _hier(nummer):
    with core.db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM collection WHERE item_id = ?", (nummer,))]


def _hier_anlegen(nummer, menge=1):
    with core.db() as conn:
        conn.execute("INSERT INTO collection (item_id, item_type, name, quantity, condition, "
                     "added_at) VALUES (?, 'minifig', ?, ?, 'used', ?)",
                     (nummer, "Figur " + nummer, menge, int(time.time())))


def test_von_zuhause_kommt_an(zuhause, gegen):
    zuhause.api("POST", "/api/collection", {"item_id": "abg001", "name": "Figur", "quantity": 2})
    abgleich.abgleichen(gegen)
    assert [z["quantity"] for z in _hier("abg001")] == [2]


def test_vom_rechner_geht_hinaus(zuhause, gegen):
    _hier_anlegen("abg002", 3)
    abgleich.abgleichen(gegen)
    assert zuhause.figur("abg002")["quantity"] == 3


def test_kein_echo(zuhause, gegen):
    """Ein zweiter Abgleich ohne Änderung schickt nichts und holt nichts."""
    _hier_anlegen("abg003")
    zuhause.api("POST", "/api/collection", {"item_id": "abg004", "name": "Figur"})
    abgleich.abgleichen(gegen)
    zweiter = abgleich.abgleichen(gegen)
    assert zweiter["geschickt"] == 0
    assert zweiter["geholt"] == 0


def test_aenderung_hier_kommt_dort_an(zuhause, gegen):
    zuhause.api("POST", "/api/collection", {"item_id": "abg005", "name": "Figur"})
    abgleich.abgleichen(gegen)
    with core.db() as conn:
        time.sleep(0.01)
        conn.execute("UPDATE collection SET quantity = 7 WHERE item_id = 'abg005'")
    abgleich.abgleichen(gegen)
    assert zuhause.figur("abg005")["quantity"] == 7


def test_loeschen_zuhause_loescht_hier(zuhause, gegen):
    zuhause.api("POST", "/api/collection", {"item_id": "abg006", "name": "Figur"})
    abgleich.abgleichen(gegen)
    zuhause.api("DELETE", f"/api/collection/{zuhause.figur('abg006')['id']}")
    abgleich.abgleichen(gegen)
    assert _hier("abg006") == []


def test_loeschen_hier_loescht_zuhause(zuhause, gegen):
    zuhause.api("POST", "/api/collection", {"item_id": "abg007", "name": "Figur"})
    abgleich.abgleichen(gegen)
    with core.db() as conn:
        conn.execute("DELETE FROM collection WHERE item_id = 'abg007'")
    abgleich.abgleichen(gegen)
    assert zuhause.figur("abg007") is None
    abgleich.abgleichen(gegen)
    assert _hier("abg007") == []


def test_dieselbe_figur_auf_beiden_seiten(zuhause, gegen):
    """Offline auf beiden Seiten erfasst: Mengen addieren wie in der App,
    danach genau eine Zeile auf jeder Seite."""
    _hier_anlegen("abg008", 2)
    zuhause.api("POST", "/api/collection", {"item_id": "abg008", "name": "Figur", "quantity": 3})
    abgleich.abgleichen(gegen)
    abgleich.abgleichen(gegen)
    assert [z["quantity"] for z in _hier("abg008")] == [5]
    assert [z["quantity"] for z in zuhause.figuren("abg008")] == [5]


def test_neuere_aenderung_gewinnt(zuhause, gegen):
    zuhause.api("POST", "/api/collection", {"item_id": "abg009", "name": "Figur"})
    abgleich.abgleichen(gegen)
    zuhause.api("PATCH", f"/api/collection/{zuhause.figur('abg009')['id']}", {"quantity": 4})
    time.sleep(0.05)
    with core.db() as conn:
        conn.execute("UPDATE collection SET quantity = 9 WHERE item_id = 'abg009'")
    abgleich.abgleichen(gegen)
    assert [z["quantity"] for z in _hier("abg009")] == [9]
    assert zuhause.figur("abg009")["quantity"] == 9


def test_geholtes_geht_nicht_zurueck(zuhause, gegen):
    """Was eben von zu Hause kam, hat hier einen neuen Stand bekommen – es
    darf im selben Lauf nicht als eigene Änderung zurückgehen. Sonst zählt
    die SE zu Hause bei jedem Abgleich alles einmal hoch."""
    zuhause.api("POST", "/api/collection", {"item_id": "abg010", "name": "Figur"})
    assert abgleich.abgleichen(gegen)["geschickt"] == 0


def test_verbinden_mit_benutzer_und_passwort(zuhause, monkeypatch):
    """Einrichten wie in der App: Adresse, Benutzer, Passwort. Gespeichert
    wird das Token, nie das Passwort – es landete sonst in jeder Sicherung."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    import desktop
    uid, name = desktop.konto()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
    with core.db() as conn:
        conn.execute("DELETE FROM settings WHERE name LIKE 'abgleich_%'")
    r = c.post("/api/abgleich/verbinden", json={
        "adresse": zuhause.adresse, "benutzer": "zuhause", "passwort": "ein-langes-passwort"})
    assert r.status_code == 200, r.text
    with core.db() as conn:
        werte = [v for (v,) in conn.execute("SELECT value FROM settings")]
    assert not any("ein-langes-passwort" in str(v) for v in werte)
    assert c.get("/api/abgleich").json()["an"] is True
    falsch = c.post("/api/abgleich/verbinden", json={
        "adresse": zuhause.adresse, "benutzer": "zuhause", "passwort": "falsch"})
    assert falsch.status_code == 400


def test_auf_dem_nas_gibt_es_den_abgleich_nicht(monkeypatch):
    from fastapi.testclient import TestClient
    import desktop
    monkeypatch.delenv("NUPPLO_DESKTOP", raising=False)
    uid, name = desktop.konto()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
    assert c.get("/api/abgleich").status_code == 404


class _Unterwegs:
    """Steht im Test für Nupplo Connect – mit einem direkten Weg zur SE zu
    Hause. Geprüft wird hier, wann der Abgleich ihn nimmt; die Leitung
    selbst prüft `windows/tests/test_unterwegs.py` gegen eine echte SE."""

    def __init__(self, zuhause):
        self.zuhause, self.gekoppelt_mit, self.vergessen_ = zuhause, None, False

    def gekoppelt(self):
        return True

    def koppeln(self, link):
        self.gekoppelt_mit = link

    def gegenstelle(self, token):
        g = abgleich.HttpGegenstelle(self.zuhause.adresse, token)
        g.weg = "connect"
        return g

    def vergessen(self):
        self.vergessen_ = True


def test_unterwegs_geht_es_ueber_connect(zuhause, gegen, monkeypatch):
    """Außer Haus antwortet die Heimnetz-Adresse nicht. Bis 0.2.0 ging die
    Windows-App dann über Connect; der Abgleich muss das auch, sonst gleicht
    sich der Laptop nur zu Hause ab."""
    monkeypatch.setattr(abgleich, "UNTERWEGS", _Unterwegs(zuhause))
    core.set_setting("abgleich_adresse", f"http://127.0.0.1:{_frei()}")   # niemand da
    core.set_setting("abgleich_token", zuhause.token)
    zuhause.api("POST", "/api/collection", {"item_id": "abg011", "name": "Figur"})
    abgleich.abgleichen()
    assert [z["quantity"] for z in _hier("abg011")] == [1]
    assert core.get_setting("abgleich_weg") == "connect"


def test_falsches_passwort_geht_nicht_ueber_connect(zuhause, gegen, monkeypatch):
    """Nur wenn die SE gar nicht antwortet, lohnt der andere Weg. Eine
    abgelaufene Anmeldung bliebe dort genauso abgelaufen – und die Meldung
    soll die Anmeldung nennen, nicht „nicht erreichbar“."""
    u = _Unterwegs(zuhause)
    u.gegenstelle = lambda token: pytest.fail("Connect bei 401 benutzt")
    monkeypatch.setattr(abgleich, "UNTERWEGS", u)
    core.set_setting("abgleich_adresse", zuhause.adresse)
    core.set_setting("abgleich_token", "abgelaufen")
    with pytest.raises(abgleich.AbgleichFehler, match="Anmeldung"):
        abgleich.abgleichen()


def test_connect_aus_auf_der_se_kippt_das_verbinden_nicht(zuhause, monkeypatch):
    """Ist Connect auf der SE aus, gibt es keinen Kopplungslink. Der Abgleich
    zu Hause soll trotzdem gehen, und der Grund steht in den Einstellungen."""
    from fastapi.testclient import TestClient
    import desktop
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    u = _Unterwegs(zuhause)
    monkeypatch.setattr(abgleich, "UNTERWEGS", u)
    uid, name = desktop.konto()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
    r = c.post("/api/abgleich/verbinden", json={
        "adresse": zuhause.adresse, "benutzer": "zuhause", "passwort": "ein-langes-passwort"})
    assert r.status_code == 200, r.text
    assert u.gekoppelt_mit is None
    assert "Portfreigabe" in c.get("/api/abgleich").json()["unterwegs_grund"]
    c.post("/api/abgleich/trennen")
    assert u.vergessen_



def test_loeschungen_eltern_vor_kindern(zuhause, gegen):
    """Am iPhone fiel am 27.09.2026 auf: Wird erst der Kauf gelöscht, zieht
    die SE die Summe des Eintrags nach, der Eintrag bekommt einen neuen
    Stand, und seine eigene Löschung galt als Konflikt – die Figur kam
    zurück. Die SE fängt das inzwischen selbst ab (`_summe_nachziehen`,
    Konfliktprüfung nur gegen Stände vor dem Push). Windows schickte bis
    0.3.2 trotzdem Kinder zuerst, anders als das iPhone (Durchsicht
    17.10.2026) – eine ältere SE zu Hause nähme das wieder übel."""
    eintrag = zuhause.api("POST", "/api/collection", {"item_id": "abg012", "name": "Figur"})
    eid = eintrag.get("id") or zuhause.figur("abg012")["id"]
    zuhause.api("POST", f"/api/collection/{eid}/purchases", {"quantity": 1, "price": 5})
    abgleich.abgleichen(gegen)
    with core.db() as conn:
        cid = conn.execute("SELECT id FROM collection WHERE item_id = 'abg012'").fetchone()[0]
        conn.execute("DELETE FROM purchases WHERE entry_id = ?", (cid,))
        conn.execute("DELETE FROM collection WHERE id = ?", (cid,))
        geschickt = int(abgleich._setting(conn, "abgleich_geschickt", "0"))
        tabellen = [s["table"] for s in abgleich._offene(conn, geschickt) if s.get("deleted")]
    assert tabellen == ["collection", "purchases"]
    abgleich.abgleichen(gegen)
    assert zuhause.figur("abg012") is None
    assert _hier("abg012") == []


class _Zaehler:
    def __init__(self, g):
        self.g, self.wege = g, []

    def get(self, weg):
        self.wege.append(weg.split("?")[0])
        return self.g.get(weg)

    def post(self, weg, daten):
        self.wege.append(weg)
        return self.g.post(weg, daten)


def test_ohne_aenderung_nur_eine_frage(zuhause, gegen):
    """Bis 0.3.2 holte und fragte Windows alle zwei Minuten alles ab, auch
    wenn sich nirgends etwas getan hatte (Durchsicht 17.10.2026). Ohne
    Änderung genügt `info`."""
    abgleich.abgleichen(gegen)            # Echo des eigenen Schickens holen
    z = _Zaehler(gegen)
    abgleich.abgleichen(z)
    assert z.wege == ["/api/sync/info"]


def test_ruhig_und_bei_fehlern_seltener():
    """Starr alle 120 s, auch wenn die SE zu Hause seit Stunden aus war."""
    p = abgleich.TAKT_SEKUNDEN
    for _ in range(10):
        p = abgleich.naechste_pause(p, None)
    assert p == abgleich.TAKT_FEHLER_MAX
    assert abgleich.naechste_pause(p, {"geholt": 1}) == abgleich.TAKT_SEKUNDEN
    ruhig = abgleich.TAKT_SEKUNDEN
    for _ in range(10):
        ruhig = abgleich.naechste_pause(ruhig, {"geholt": 0, "geschickt": 0, "abgelehnt": 0})
    assert abgleich.TAKT_SEKUNDEN < ruhig == abgleich.TAKT_RUHIG_MAX


def test_aenderung_hier_wartet_keine_ruhepause_ab(gegen, monkeypatch):
    """Mit langer Ruhepause läge eine Figur, die man eben erfasst hat, sonst
    bis zu zehn Minuten nur hier."""
    monkeypatch.setattr(abgleich, "SCHAU_SEKUNDEN", 0.05)
    import threading
    fertig = threading.Event()
    t = threading.Thread(target=lambda: (abgleich._warten(30), fertig.set()), daemon=True)
    t.start()
    time.sleep(0.2)
    assert not fertig.is_set()
    _hier_anlegen("abg013")
    assert fertig.wait(2)


def test_ohne_konto_kein_database_locked(zuhause, gegen):
    """Gibt es hier noch kein Konto, legte der Abgleich es in einer zweiten
    Verbindung an, während die erste offen war – „database is locked“
    (Durchsicht 17.10.2026). Beim Start legt `einrichten()` es zwar an, eine
    zurückgespielte Sicherung ohne Eigner hätte aber keins."""
    zuhause.api("POST", "/api/collection", {"item_id": "abg014", "name": "Figur"})
    with core.db() as conn:
        admins = [dict(r) for r in conn.execute("SELECT * FROM users WHERE is_admin = 1")]
        conn.execute("UPDATE users SET is_admin = 0, username = username || '-alt' "
                     "WHERE is_admin = 1")
        # Wie nach einer zurückgespielten Sicherung zu Hause: Der Abgleich
        # schreibt den neuen Stand, bevor er das Konto braucht.
        conn.execute("UPDATE settings SET value = 'alt' WHERE name = 'abgleich_epoch'")
    try:
        abgleich.abgleichen(gegen)
        assert [z["quantity"] for z in _hier("abg014")] == [1]
    finally:
        with core.db() as conn:
            conn.execute("UPDATE users SET is_admin = 0, username = 'neu-' || id "
                         "WHERE is_admin = 1")
            for a in admins:
                conn.execute("UPDATE users SET is_admin = 1, username = ? WHERE id = ?",
                             (a["username"], a["id"]))


def test_man_sieht_mit_welcher_se_und_auf_welchem_weg(zuhause, gegen):
    """Sven 18.10.2026: „kann man in den Apps auch vermerken, an welcher
    Instanz sie verbunden ist?“ Bis dahin stand nur die Adresse da – wer
    zu Hause die SE benannt hat, erkannte sie daran nicht. Den Namen kennt
    nur die SE; er muss mit dem Abgleich herüberkommen und auch eine
    spätere Umbenennung dort mitnehmen."""
    from fastapi.testclient import TestClient
    uid, name = desktop.konto()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, name, True)
    zuhause.api("POST", "/api/settings/owner_name", {"name": "Sammlung Keller"})
    try:
        abgleich.abgleichen(gegen)
        stand = c.get("/api/abgleich").json()
        assert stand["name"] == "Sammlung Keller"
        assert stand["weg"] == "heimnetz"
        zuhause.api("POST", "/api/settings/owner_name", {"name": "Sammlung Dach"})
        abgleich.abgleichen(gegen)
        assert c.get("/api/abgleich").json()["name"] == "Sammlung Dach"
    finally:
        zuhause.api("POST", "/api/settings/owner_name", {"name": ""})


# ---------------------------------------------------------------- Preise

def _preis_zuhause(zuhause, nummer, neu, zeit):
    zuhause.sql("UPDATE collection SET price_new = ?, price_used = ?, price_data = ?, "
                "price_updated_at = ? WHERE item_id = ?",
                neu, neu, json.dumps({"stand": zeit}), zeit, nummer)


def test_leere_preise_hier_loeschen_sie_zu_hause_nicht(zuhause, gegen):
    """08.11.2026: Die Windows-App leerte während des BrickLink-Ausfalls
    ihre Figurenpreise (Verfall nach 26 Stunden), der Trigger zog die Zeilen
    hoch, und der Abgleich schickte 464 Sammlungszeilen mit leeren Preisen an
    seine SE. Die übernahm sie, und die Preise waren auch dort weg."""
    zuhause.api("POST", "/api/collection", {"item_id": "abg020", "name": "Figur"})
    _preis_zuhause(zuhause, "abg020", 12.5, int(time.time()) - 3600)
    abgleich.abgleichen(gegen)
    assert _hier("abg020")[0]["price_used"] == 12.5
    with core.db() as conn:
        conn.execute("UPDATE collection SET price_new = NULL, price_used = NULL, "
                     "price_data = NULL WHERE item_id = 'abg020'")
    assert abgleich.abgleichen(gegen)["geschickt"] == 0
    dort = zuhause.figur("abg020")
    assert dort["price_used"] == 12.5 and dort["price_new"] == 12.5


def test_echte_aenderung_geht_ohne_preise_hinaus(zuhause, gegen):
    """Dieselbe Lage, aber hier wurde auch die Menge geändert: Die Menge
    kommt zu Hause an, der Preis dort bleibt."""
    zuhause.api("POST", "/api/collection", {"item_id": "abg021", "name": "Figur"})
    _preis_zuhause(zuhause, "abg021", 8.0, int(time.time()) - 3600)
    abgleich.abgleichen(gegen)
    time.sleep(0.01)
    with core.db() as conn:
        conn.execute("UPDATE collection SET quantity = 3, price_new = NULL, "
                     "price_used = NULL, price_data = NULL WHERE item_id = 'abg021'")
    abgleich.abgleichen(gegen)
    dort = zuhause.figur("abg021")
    assert dort["quantity"] == 3
    assert dort["price_used"] == 8.0


def test_preis_hier_bleibt_wenn_von_dort_keiner_kommt(zuhause, gegen):
    """Umgekehrt: Eine SE, die ihre Preise verloren hat (wie eine Test-SE nach dem
    Vorfall), soll sie beim Holen nicht auch hier löschen. Ein Preis
    verschwindet nie (30.10.2026) – die Menge von dort gilt aber."""
    zuhause.api("POST", "/api/collection", {"item_id": "abg022", "name": "Figur"})
    _preis_zuhause(zuhause, "abg022", 5.0, int(time.time()) - 3600)
    abgleich.abgleichen(gegen)
    zuhause.sql("UPDATE collection SET quantity = 4, price_new = NULL, price_used = NULL, "
                "price_data = NULL WHERE item_id = 'abg022'")
    abgleich.abgleichen(gegen)
    hier = _hier("abg022")[0]
    assert hier["quantity"] == 4
    assert hier["price_used"] == 5.0


def test_neuerer_preis_hier_haelt_aenderung_von_dort_nicht_auf(zuhause, gegen):
    """Ein Preis, der hier nach dem letzten Abgleich neu kam, setzt über den
    Trigger auch `updated_at` neu. Galt das als „hier neuer geändert“, blieb
    eine Mengenänderung von zu Hause hier liegen."""
    zuhause.api("POST", "/api/collection", {"item_id": "abg023", "name": "Figur"})
    abgleich.abgleichen(gegen)
    zuhause.api("PATCH", f"/api/collection/{zuhause.figur('abg023')['id']}", {"quantity": 6})
    time.sleep(0.05)
    with core.db() as conn:
        conn.execute("UPDATE collection SET price_used = 3.0, price_updated_at = ? "
                     "WHERE item_id = 'abg023'", (int(time.time()),))
    abgleich.abgleichen(gegen)
    hier = _hier("abg023")[0]
    assert hier["quantity"] == 6
    assert hier["price_used"] == 3.0
