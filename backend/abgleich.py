"""Abgleich mit einer anderen Nupplo SE – die Geräteseite von `sync.py`.

Für Nupplo für Windows (Desktop-Modus): Die SE im Programm hat immer ihre
eigene Sammlung und gleicht sie wahlweise mit der SE zu Hause ab, genau wie
iPhone und Mac. Sven hat das am 16.10.2026 so entschieden („Es sollen alle
das selbe können“). Bis dahin zeigte die Windows-App im Modus „Mit meiner
Nupplo SE“ nur deren Oberfläche, ohne eigene Daten.

**Dieselbe Schnittstelle wie die Apps** (`/api/sync/info`, `/pull`, `/push`,
beschrieben in `nupplo-ios/docs/SYNC-API.md`), dieselben Regeln wie
`SyncMotor.swift`:

- Erst holen, dann schicken.
- Konflikte: Die neueste Änderung je Satz gewinnt, bei Gleichstand die SE.
- Doppelter Artikel: Mengen addieren, der Satz hier übernimmt die UUID der SE.

**Was hier einfacher ist als in der App.** Die SE hat `uuid`, `updated_at`,
`rev` und Löschvermerke schon, als Server-Seite gebaut (Trigger in
`sync.py`). Wer hier etwas ändert – Web-App, Live-Scanner, Import –, bekommt
von den Triggern einen neuen lokalen `rev`. Geschickt wird, was seit dem
letzten Mal einen neuen `rev` hat. Übersetzen (Nummern ↔ UUIDs, Benutzer)
und Prüfen übernimmt `sync._eingang`, dieselbe Stelle, die auch die SE zu
Hause benutzt.

**Das eigene Echo.** Ein Satz von der SE bekommt hier beim Übernehmen
ebenfalls einen neuen `rev`. Ohne Merker ginge er sofort zurück. Darum steht
in `abgleich_saetze` je Satz, welchen `rev` er hier zuletzt durch den
Abgleich bekam (`lokal_rev`) und welchen er dort hat (`server_rev`, der
`base_rev` beim Schicken).

**Preise bleiben auf dem Gerät.** Die Preisfelder (`price_…`) gehen nicht
hinaus, und eine Änderung nur an ihnen ist keine Änderung für den Abgleich.
Am 08.11.2026 schickte die Windows-App auf einem Test-PC 464 Sammlungszeilen mit
leeren Preisen an seine SE, und die SE löschte damit ihre Preise. Die
Windows-App hatte sie vorher selbst geleert (Verfall nach 26 Stunden während
des BrickLink-Ausfalls, 3.9.31 hatte das Labor noch nicht), der Trigger zog
`rev` hoch, und `_offene` schickte die ganze Zeile. Preise holt jede Seite
selbst; was hier zählt, sind Menge, Zustand, Notizen und dergleichen. Dafür
steht in `abgleich_saetze.abdruck`, wie die übrigen Felder zuletzt
übereinstimmten. Beim Holen bleibt ein Preis hier stehen, wenn von dort
keiner oder ein älterer kommt – ein Preis verschwindet nie (30.10.2026).
"""
import hashlib
import json
import threading
import secrets
import time

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import core
import desktop
import sync

router = APIRouter()

SEITE = 500
# Takt des Abgleichs im Hintergrund. Ruhig wird seltener gefragt, nach
# Fehlern immer seltener – bis 0.3.2 fragte Windows starr alle 120 s, auch
# wenn die SE zu Hause seit Stunden aus war (Durchsicht 17.10.2026). Eine
# Änderung hier geht trotzdem nach höchstens SCHAU_SEKUNDEN hinaus.
TAKT_SEKUNDEN = 120
TAKT_RUHIG_MAX = 600
TAKT_FEHLER_MAX = 1800
SCHAU_SEKUNDEN = 15
ZEITLIMIT = 30


def _preisfeld(k: str) -> bool:
    return k.startswith("price_")


def _ohne_preise(felder: dict) -> dict:
    return {k: v for k, v in felder.items() if not _preisfeld(k)}


def _abdruck(felder: dict) -> str:
    """Fingerabdruck der Felder ohne Preise – gleich, wenn sich außer
    Preisen nichts geändert hat."""
    roh = json.dumps(_ohne_preise(felder), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(roh.encode()).hexdigest()


class AbgleichFehler(Exception):
    pass


class NichtErreichbar(AbgleichFehler):
    """Die SE antwortet auf diesem Weg gar nicht – dann lohnt der andere."""


# Abgleich unterwegs über Nupplo Connect. Setzt das Windows-Programm beim
# Start (`nupplo_bruecke/unterwegs.py`), weil Leitung, Noise und der
# Geräteschlüssel im Schlüsselbund dort liegen. Ohne Programm (NAS, Tests)
# bleibt es None, und abgeglichen wird nur direkt.
# Erwartet: gekoppelt() -> bool, koppeln(link), gegenstelle(token), vergessen().
UNTERWEGS = None


# ------------------------------------------------------------ Gegenstelle

class HttpGegenstelle:
    """Die SE zu Hause, direkt im Heimnetz. Unterwegs tritt an ihre Stelle
    eine Gegenstelle über Nupplo Connect mit denselben zwei Methoden."""

    def __init__(self, adresse: str, token: str = ""):
        self.adresse = adresse.rstrip("/")
        self.token = token

    def _antwort(self, r):
        return antwort_lesen(r.status_code, r.content, r.url)

    def _kopf(self):
        return {"Authorization": "Bearer " + self.token} if self.token else {}

    # Kurze Frist für den Verbindungsaufbau: Unterwegs antwortet die
    # Heimnetz-Adresse gar nicht, und dann soll es gleich über Connect gehen,
    # nicht erst nach einer halben Minute.
    def get(self, weg: str):
        try:
            r = requests.get(self.adresse + weg, headers=self._kopf(), timeout=(4, ZEITLIMIT))
        except requests.RequestException:
            raise NichtErreichbar("Deine Nupplo SE ist nicht erreichbar")
        return self._antwort(r)

    def post(self, weg: str, daten: dict):
        try:
            r = requests.post(self.adresse + weg, json=daten, headers=self._kopf(),
                              timeout=(4, ZEITLIMIT))
        except requests.RequestException:
            raise NichtErreichbar("Deine Nupplo SE ist nicht erreichbar")
        return self._antwort(r)


def antwort_lesen(status: int, inhalt: bytes, weg: str):
    """Für beide Wege gleich: direkt und über Connect."""
    def detail():
        try:
            return json.loads(inhalt).get("detail")
        except (ValueError, AttributeError):
            return None
    if status == 401:
        raise AbgleichFehler("Die Anmeldung bei deiner Nupplo SE ist abgelaufen")
    if status == 404 and "/api/sync/" in weg:
        raise AbgleichFehler("Deine Nupplo SE ist zu alt für den Abgleich")
    if status >= 400:
        raise AbgleichFehler(str(detail() or f"Nupplo SE antwortet mit {status}"))
    return json.loads(inhalt)


class MitAusweg:
    """Erst direkt, und wenn die SE dort gar nicht antwortet, für den Rest
    des Laufs über Nupplo Connect – so wie die App zu Hause und unterwegs."""

    def __init__(self, direkt, unterwegs):
        self.direkt = direkt
        self._unterwegs = unterwegs          # erst bei Bedarf bauen
        self.aktiv = direkt
        self.weg = "heimnetz"

    def _rufen(self, name, *args):
        try:
            return getattr(self.aktiv, name)(*args)
        except NichtErreichbar:
            if self.aktiv is not self.direkt:
                raise
            andere = self._unterwegs()
            if andere is None:
                raise
            self.aktiv, self.weg = andere, "connect"
            return getattr(andere, name)(*args)

    def get(self, weg):
        return self._rufen("get", weg)

    def post(self, weg, daten):
        return self._rufen("post", weg, daten)


# ------------------------------------------------------------ Merker

def migrieren(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS abgleich_saetze ("
                 "tabelle TEXT NOT NULL, uuid TEXT NOT NULL, server_rev INTEGER, "
                 "lokal_rev INTEGER, PRIMARY KEY (tabelle, uuid))")
    # Seit 0.5.3: welche Felder (ohne Preise) beide Seiten zuletzt gleich
    # hatten – siehe Kopf. Bestand ohne Abdruck geht einmal ohne Preise hinaus.
    if "abdruck" not in {r[1] for r in conn.execute("PRAGMA table_info(abgleich_saetze)")}:
        conn.execute("ALTER TABLE abgleich_saetze ADD COLUMN abdruck TEXT")
    # Die unterlegene Fassung eines Konflikts geht nicht verloren – wie
    # `sync_konflikte` in der App.
    conn.execute("CREATE TABLE IF NOT EXISTS abgleich_konflikte ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, tabelle TEXT NOT NULL, "
                 "uuid TEXT NOT NULL, grund TEXT NOT NULL, meldung TEXT, "
                 "fassung TEXT, zeit INTEGER NOT NULL)")


def _merker(conn, t, u):
    return conn.execute("SELECT server_rev, lokal_rev, abdruck FROM abgleich_saetze "
                        "WHERE tabelle = ? AND uuid = ?", (t, u)).fetchone()


def _merken(conn, t, u, server_rev=..., lokal_rev=..., abdruck=...):
    alt = _merker(conn, t, u)

    def wert(neu, name):
        if neu is not ...:
            return neu
        return alt[name] if alt else None
    conn.execute("INSERT INTO abgleich_saetze (tabelle, uuid, server_rev, lokal_rev, abdruck) "
                 "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tabelle, uuid) DO UPDATE SET "
                 "server_rev = excluded.server_rev, lokal_rev = excluded.lokal_rev, "
                 "abdruck = excluded.abdruck",
                 (t, u, wert(server_rev, "server_rev"), wert(lokal_rev, "lokal_rev"),
                  wert(abdruck, "abdruck")))


def _abdruck_hier(conn, t, u, karten):
    row = conn.execute(f"SELECT * FROM {t} WHERE uuid = ?", (u,)).fetchone()
    return _abdruck(sync._satz(t, row, karten, True)["fields"]) if row is not None else None


def _konflikt(conn, t, u, grund, meldung="", fassung=None):
    conn.execute("INSERT INTO abgleich_konflikte (tabelle, uuid, grund, meldung, fassung, zeit) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (t, u, grund, meldung or "", json.dumps(fassung, ensure_ascii=False)
                  if fassung is not None else None, int(time.time())))


def _stand(conn) -> int:
    return conn.execute("SELECT rev FROM sync_stand WHERE id = 1").fetchone()[0]


def _setting(conn, name, vorgabe=""):
    r = conn.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
    return r[0] if r and r[0] is not None else vorgabe


def _setzen(conn, name, wert):
    conn.execute("INSERT INTO settings (name, value) VALUES (?, ?) "
                 "ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, str(wert)))


def _benutzer(conn) -> dict:
    """Unbekannte Benutzernamen von dort werden zu diesem einen Konto."""
    uid, name = desktop.konto(conn)
    return {"id": uid, "name": name, "is_dealer": True}


# ------------------------------------------------------------ Holen

def _zusammenfuehren(conn, t, andere, u, werte, c):
    """Ein Satz hier, den die SE noch nie gesehen hat, belegt denselben
    Artikel wie einer von dort. Wie in der App: Mengen addieren, die UUID
    der SE übernehmen – die Summe geht beim nächsten Schicken hinaus."""
    neu = dict(werte)
    if t == "collection":
        neu["quantity"] = min(9999, (andere["quantity"] or 0) + (werte.get("quantity") or 0))
        # Leere Felder von hier mit denen von dort füllen, gesetzte behalten.
        for k, v in list(neu.items()):
            if k != "quantity" and andere[k] not in (None, ""):
                neu.pop(k)
    spalten = ", ".join(f"{k} = ?" for k in neu)
    conn.execute(f"UPDATE {t} SET uuid = ?{', ' + spalten if spalten else ''} WHERE id = ?",
                 (u, *neu.values(), andere["id"]))
    # server_rev: die Fassung dort; lokal_rev bleibt leer – der Satz ist
    # damit „geändert“ und geht mit der Summe hinaus.
    _merken(conn, t, u, server_rev=c["rev"], lokal_rev=None, abdruck=None)
    if t != "collection":
        jetzt = conn.execute(f"SELECT rev FROM {t} WHERE uuid = ?", (u,)).fetchone()[0]
        _merken(conn, t, u, lokal_rev=jetzt)


def _uebernehmen(conn, c: dict, karten, user, geschickt: int, erzwingen=False) -> bool:
    """Einen Satz von der SE hier anwenden – True, wenn sich hier etwas änderte. Wirft `sync._Abgelehnt` (orphan)
    bei einem Kind vor seinem Elternsatz – der kommt im selben Holen später."""
    t, u = c["table"], c["uuid"]
    if t not in sync.TABELLEN:
        return False
    merker = _merker(conn, t, u)
    if not erzwingen and merker and merker["server_rev"] == c.get("rev"):
        return False                             # eigenes Echo
    row = conn.execute(f"SELECT * FROM {t} WHERE uuid = ?", (u,)).fetchone()
    geaendert_hier = row is not None and (merker is None or row["rev"] != merker["lokal_rev"])
    # Nur Preise geändert zählt nicht: Sonst hielte ein frisch geholter
    # Preis hier eine echte Änderung von dort auf (der Trigger setzt dabei
    # auch `updated_at` neu).
    if geaendert_hier and merker is not None and merker["abdruck"] \
            and _abdruck_hier(conn, t, u, karten) == merker["abdruck"]:
        geaendert_hier = False
    zeit = int(c.get("updated_at") or 0)

    # Hier neuer geändert? Dann bleibt es, und es geht beim Schicken hinaus –
    # mit dem neuen Stand von dort als base_rev, damit die SE es annimmt.
    if not erzwingen and geaendert_hier and (row["updated_at"] or 0) > zeit:
        _merken(conn, t, u, server_rev=c.get("rev"))
        return False

    if c.get("deleted"):
        if row is not None:
            sync._loeschen(conn, t, row, zeit)
        stein = conn.execute("SELECT rev FROM sync_tombstones WHERE tabelle = ? AND uuid = ?",
                             (t, u)).fetchone()
        _merken(conn, t, u, server_rev=c.get("rev"), lokal_rev=stein[0] if stein else None)
        return row is not None

    if row is None and not erzwingen:
        # Hier gelöscht und noch nicht geschickt, und zwar später?
        stein = conn.execute("SELECT rev, deleted_at FROM sync_tombstones "
                             "WHERE tabelle = ? AND uuid = ?", (t, u)).fetchone()
        if stein and stein["rev"] > geschickt and (stein["deleted_at"] or 0) > zeit:
            _merken(conn, t, u, server_rev=c.get("rev"))
            return False

    werte = sync._eingang(conn, t, c.get("fields") or {}, karten, user, True)
    schluessel = sync.TABELLEN[t].get("schluessel")
    if row is None and schluessel:
        kv = [werte.get(s) for s in schluessel]
        if None not in kv:
            andere = conn.execute(
                f"SELECT * FROM {t} WHERE " + " AND ".join(f"{s} = ?" for s in schluessel)
                + " AND uuid <> ?", (*kv, u)).fetchone()
            if andere is not None:
                _zusammenfuehren(conn, t, andere, u, werte, c)
                return True

    if row is not None and _preis_bleibt(row, werte):
        werte = _ohne_preise(werte)
    if row is not None:
        spalten = ", ".join(f"{k} = ?" for k in werte)
        conn.execute(f"UPDATE {t} SET {spalten + ', ' if spalten else ''}updated_at = ? "
                     "WHERE uuid = ?", (*werte.values(), zeit, u))
    else:
        namen = ["uuid", *werte, "updated_at"]
        conn.execute(f"INSERT INTO {t} ({', '.join(namen)}) VALUES "
                     f"({', '.join('?' * len(namen))})", (u, *werte.values(), zeit))
    jetzt = conn.execute(f"SELECT rev FROM {t} WHERE uuid = ?", (u,)).fetchone()[0]
    _merken(conn, t, u, server_rev=c.get("rev"), lokal_rev=jetzt,
            abdruck=_abdruck_hier(conn, t, u, karten))
    return True


def _preis_bleibt(row, werte: dict) -> bool:
    """Den Preis hier behalten, wenn von dort keiner oder ein älterer kommt.
    Eine SE, die ihre Preise verloren hat (08.11.2026), soll sie nicht
    auch hier löschen."""
    spalten = row.keys()
    if "price_updated_at" not in spalten or not any(_preisfeld(k) for k in werte):
        return False
    hier = any(row[k] is not None for k in ("price_new", "price_used", "price_data")
               if k in spalten)
    if not hier:
        return False
    dort = any(werte.get(k) is not None for k in ("price_new", "price_used", "price_data"))
    return not dort or (werte.get("price_updated_at") or 0) < (row["price_updated_at"] or 0)


def holen(gegen, conn) -> int:
    """Alles seit dem letzten Stand holen und anwenden. Gibt die Zahl der
    übernommenen Sätze zurück."""
    info = gegen.get("/api/sync/info")
    if info.get("protocol") != sync.PROTOKOLL:
        raise AbgleichFehler("Deine Nupplo SE spricht eine andere Fassung des Abgleichs")
    # Bei jedem Takt, nicht nur beim Verbinden: Wer den Namen zu Hause
    # ändert, sieht ihn hier beim nächsten Abgleich.
    _setzen(conn, "abgleich_name", info.get("name") or "")
    seit = int(_setting(conn, "abgleich_seit", "0") or 0)
    neu_begonnen = _setting(conn, "abgleich_epoch") != info.get("epoch")
    # Nichts Neues dort: kein Pull. Der Abgleich fragt alle paar Minuten,
    # und meistens hat sich zu Hause nichts getan (Durchsicht 17.10.2026).
    if not neu_begonnen and info.get("rev") == seit:
        return 0
    if neu_begonnen:
        # Eine Sicherung wurde dort zurückgespielt: von vorn holen. Die
        # Merker bleiben – die UUIDs stecken in der Sicherung.
        seit = 0
        _setzen(conn, "abgleich_epoch", info.get("epoch") or "")
    karten = sync._Karten(conn)
    user = _benutzer(conn)
    geschickt = int(_setting(conn, "abgleich_geschickt", "0") or 0)
    spaeter, anzahl = [], 0
    while True:
        seite = gegen.get(f"/api/sync/pull?since={seit}&limit={SEITE}")
        for c in seite.get("changes", []):
            try:
                anzahl += _uebernehmen(conn, c, karten, user, geschickt)
            except sync._Abgelehnt as e:
                if e.grund == "orphan":
                    spaeter.append(c)
                else:
                    _konflikt(conn, c["table"], c["uuid"], e.grund, e.meldung, c)
        seit = seite["rev"]
        if not seite.get("more"):
            break
    # Kinder, die vor ihrem Elternsatz kamen.
    for c in spaeter:
        try:
            anzahl += _uebernehmen(conn, c, karten, user, geschickt)
        except sync._Abgelehnt as e:
            _konflikt(conn, c["table"], c["uuid"], e.grund, e.meldung, c)
    _setzen(conn, "abgleich_seit", seit)
    return anzahl


# ------------------------------------------------------------ Schicken

def _alle_merker(conn, t) -> dict:
    """Alle Merker einer Tabelle auf einmal – je Zeile einzeln gefragt
    dauerte das Sammeln bei einer großen Sammlung spürbar (Durchsicht
    17.10.2026)."""
    return {u: (s, l, a) for u, s, l, a in conn.execute(
        "SELECT uuid, server_rev, lokal_rev, abdruck FROM abgleich_saetze WHERE tabelle = ?",
        (t,))}


def _offene(conn, geschickt: int) -> list:
    karten = sync._Karten(conn)
    saetze, loeschen = [], []
    for t in sync.TABELLEN:
        merker = _alle_merker(conn, t)
        for row in conn.execute(f"SELECT * FROM {t} WHERE rev > ? ORDER BY rev", (geschickt,)):
            server_rev, lokal_rev, abdruck = merker.get(row["uuid"], (None, None, None))
            if lokal_rev == row["rev"]:
                continue
            s = sync._satz(t, row, karten, True)
            neu = _abdruck(s["fields"])
            if abdruck and neu == abdruck:
                # Nur Preise anders – nichts zu schicken (siehe Kopf).
                _merken(conn, t, row["uuid"], lokal_rev=row["rev"])
                continue
            saetze.append({"table": t, "uuid": s["uuid"], "updated_at": s["updated_at"],
                           "base_rev": server_rev,
                           "fields": _ohne_preise(s["fields"]), "_lokal": row["rev"],
                           "_abdruck": neu})
        for st in conn.execute("SELECT uuid, rev, deleted_at FROM sync_tombstones "
                               "WHERE tabelle = ? AND rev > ? ORDER BY rev", (t, geschickt)):
            server_rev, lokal_rev, _ = merker.get(st["uuid"], (None, None, None))
            # Nie dort gewesen: nichts zu löschen.
            if server_rev is None or lokal_rev == st["rev"]:
                continue
            loeschen.append({"table": t, "uuid": st["uuid"], "updated_at": st["deleted_at"],
                             "base_rev": server_rev, "deleted": True,
                             "_lokal": st["rev"]})
    # Eltern zuerst anlegen – und auch zuerst löschen, wie das iPhone
    # (`SyncMotor.swift`). Kinder zuerst zog am 27.09.2026 auf der SE die
    # Summe des Eintrags nach, dessen eigene Löschung galt danach als
    # Konflikt, und die Figur kam zurück. Die SE fängt das heute selbst ab,
    # eine ältere zu Hause nicht. Die Kinder gehen dort mit; ihre Löschung
    # trifft danach auf „schon weg“, und das gilt als angenommen.
    return saetze + loeschen


def schicken(gegen, conn) -> tuple[int, int]:
    geschickt = int(_setting(conn, "abgleich_geschickt", "0") or 0)
    bis = _stand(conn)
    if bis == geschickt:
        return 0, 0                       # hier hat sich nichts geändert
    offen = _offene(conn, geschickt)
    angenommen = abgelehnt = 0
    for i in range(0, len(offen), SEITE):
        teil = offen[i:i + SEITE]
        lokal = {(s["table"], s["uuid"]): s["_lokal"] for s in teil}
        abdrucke = {(s["table"], s["uuid"]): s.get("_abdruck") for s in teil}
        antwort = gegen.post("/api/sync/push", {"changes": [
            {k: v for k, v in s.items() if not k.startswith("_")} for s in teil]})
        for a in antwort.get("accepted", []):
            _merken(conn, a["table"], a["uuid"], server_rev=a["rev"],
                    lokal_rev=lokal.get((a["table"], a["uuid"])),
                    abdruck=abdrucke.get((a["table"], a["uuid"])))
            angenommen += 1
        for r in antwort.get("rejected", []):
            abgelehnt += 1
            _abgelehnt(conn, r, lokal.get((r["table"], r["uuid"])))
    # Was beim Behandeln einer Ablehnung neu geändert wurde, hat einen
    # höheren Stand als `bis` und geht beim nächsten Mal hinaus.
    _setzen(conn, "abgleich_geschickt", bis)
    return angenommen, abgelehnt


def _abgelehnt(conn, r: dict, lokal_rev) -> None:
    t, u, grund = r["table"], r["uuid"], r.get("reason", "")
    server = r.get("server")
    if grund == "conflict" and server:
        # Dort neuer: deren Fassung gilt, unsere wird aufgehoben.
        row = conn.execute(f"SELECT * FROM {t} WHERE uuid = ?", (u,)).fetchone()
        if row is not None:
            _konflikt(conn, t, u, grund, "", sync._satz(t, row, sync._Karten(conn), True))
        try:
            _uebernehmen(conn, server, sync._Karten(conn), _benutzer(conn), 0, erzwingen=True)
        except sync._Abgelehnt as e:
            _konflikt(conn, t, u, e.grund, e.meldung, server)
        return
    if grund == "duplicate" and server and t in ("collection", "wanted"):
        row = conn.execute(f"SELECT * FROM {t} WHERE uuid = ?", (u,)).fetchone()
        if row is None:
            return
        alt = _merker(conn, t, u)
        neu = {"uuid": server["uuid"]}
        if t == "collection":
            neu["quantity"] = min(9999, (row["quantity"] or 0)
                                  + int((server.get("fields") or {}).get("quantity") or 0))
        spalten = ", ".join(f"{k} = ?" for k in neu)
        conn.execute(f"UPDATE {t} SET {spalten} WHERE id = ?", (*neu.values(), row["id"]))
        _merken(conn, t, server["uuid"], server_rev=server.get("rev"), lokal_rev=None)
        if alt and alt["server_rev"] is not None:
            # Die SE kannte den Satz hier schon unter der alten UUID: dort
            # löschen, sonst stünde die Figur zweimal da.
            rev = sync._naechster_stand(conn)
            conn.execute("INSERT OR REPLACE INTO sync_tombstones (tabelle, uuid, rev, deleted_at) "
                         "VALUES (?, ?, ?, ?)", (t, u, rev, int(time.time() * 1000)))
        return
    # forbidden, invalid, orphan: nicht bei jedem Lauf erneut schicken.
    # Erst eine neue Änderung hier versucht es wieder.
    _konflikt(conn, t, u, grund, r.get("message", ""), server)
    _merken(conn, t, u, lokal_rev=lokal_rev)


# ------------------------------------------------------------ Lauf

_sperre = threading.Lock()


def gegenstelle():
    """Die Gegenstelle aus den Einstellungen. `connect_weg` setzt das
    Windows-Programm, wenn die SE zu Hause nicht direkt erreichbar ist."""
    adresse = core.get_setting("abgleich_adresse")
    token = core.get_setting("abgleich_token")
    if not adresse or not token:
        return None
    direkt = HttpGegenstelle(adresse, token)
    if UNTERWEGS is None or not UNTERWEGS.gekoppelt():
        return direkt
    return MitAusweg(direkt, lambda: UNTERWEGS.gegenstelle(token))


def abgleichen(gegen=None) -> dict:
    """Einmal holen und schicken. Läuft nie zweimal gleichzeitig."""
    gegen = gegen or gegenstelle()
    if gegen is None:
        raise AbgleichFehler("Kein Abgleich eingerichtet")
    with _sperre:
        with core.db() as conn:
            migrieren(conn)
            geholt = holen(gegen, conn)
        with core.db() as conn:
            angenommen, abgelehnt = schicken(gegen, conn)
            # Was eine Ablehnung hier geändert hat (zusammengeführte
            # Mengen), gleich hinterher – sonst erst im nächsten Takt.
            if abgelehnt:
                schicken(gegen, conn)
        _verbrauch_teilen(gegen)
        core.set_setting("abgleich_zuletzt", str(int(time.time())))
        core.set_setting("abgleich_weg", getattr(gegen, "weg", "heimnetz"))
        core.set_setting("abgleich_fehler", "")
    return {"geholt": geholt, "geschickt": angenommen, "abgelehnt": abgelehnt}


def _verbrauch_teilen(gegen) -> None:
    """BrickLink-Verbrauch dieses Geräts an die SE melden und den der anderen
    Geräte mit demselben Schlüssel mitnehmen (25.11.2026). Ohne eigenen
    Schlüssel nichts. Eine ältere SE kennt den Weg nicht (404) – dann zählt
    jedes Gerät weiter für sich, und der Abgleich selbst läuft wie immer."""
    import integrations
    abdruck = integrations.schluessel_abdruck()
    if not abdruck:
        return
    geraet = core.get_setting("bl_geraet_name")
    if not geraet:
        geraet = "Windows-" + secrets.token_hex(3)
        core.set_setting("bl_geraet_name", geraet)
    tag = time.strftime("%Y-%m-%d")
    try:
        antwort = gegen.post("/api/sync/bl-verbrauch", {
            "tag": tag, "schluessel": abdruck, "geraet": geraet,
            "n": integrations.verbrauch_heute(),
            "erschoepft": core.get_setting("bl_erschoepft_tag") == tag})
    except Exception:
        return
    if not isinstance(antwort, dict) or antwort.get("tag") != tag:
        return
    core.set_setting("bl_andere_tag", tag)
    core.set_setting("bl_andere_n", str(int(antwort.get("summe") or 0)))
    core.set_setting("bl_andere_geraete", str(len(antwort.get("andere") or [])))
    core.set_setting("bl_andere_erschoepft", "1" if antwort.get("erschoepft") else "0")


def naechste_pause(pause: float, ergebnis: dict | None) -> float:
    """Wie lange bis zum nächsten Abgleich. `ergebnis` None heißt Fehler."""
    if ergebnis is None:
        return min(max(pause, TAKT_SEKUNDEN) * 2, TAKT_FEHLER_MAX)
    if ergebnis.get("geholt") or ergebnis.get("geschickt") or ergebnis.get("abgelehnt"):
        return TAKT_SEKUNDEN
    return min(pause * 1.5, TAKT_RUHIG_MAX)


def _warten(pause: float) -> None:
    """In kleinen Schritten warten: Was hier geändert wird, soll nicht bis
    zum Ende einer langen Ruhepause liegen bleiben."""
    seit = time.monotonic()
    with core.db() as conn:
        stand = _stand(conn)
    while time.monotonic() - seit < pause:
        time.sleep(min(SCHAU_SEKUNDEN, pause))
        with core.db() as conn:
            if _stand(conn) != stand:
                return


def waechter():
    """Im Hintergrund, solange der Abgleich an ist."""
    pause = TAKT_SEKUNDEN
    welcher = "abgleich_fehler"
    while True:
        try:                                  # nie den Faden verlieren
            _warten(pause)
            if core.get_setting("abgleich_an") == "1":
                welcher = "abgleich_fehler"
                pause = naechste_pause(pause, abgleichen())
            elif core.get_setting("ordner_an") == "1":
                # Derselbe Takt für den Cloud-Ordner: Was andere Geräte
                # dort ablegen, kommt ohne Zutun hier an, nur eben beim
                # nächsten Blick in den Ordner.
                import ordner
                welcher = "ordner_fehler"
                pause = naechste_pause(pause, ordner.abgleichen())
            else:
                pause = TAKT_SEKUNDEN
        except Exception as e:
            core.set_setting(welcher, str(e)[:300])
            pause = naechste_pause(pause, None)


# ------------------------------------------------------------ Endpunkte

def _admin():
    import main
    return main.admin_user


def _nur_desktop():
    if not desktop.an():
        raise HTTPException(404, "Nicht gefunden")


class Verbinden(BaseModel):
    adresse: str = Field(min_length=4, max_length=300)
    benutzer: str = Field(min_length=1, max_length=60)
    passwort: str = Field(default="", max_length=300)
    code: str = Field(default="", max_length=20)
    challenge: str = Field(default="", max_length=2000)


@router.get("/api/abgleich")
def abgleich_stand(user: dict = Depends(_admin())):
    _nur_desktop()
    with core.db() as conn:
        migrieren(conn)
        konflikte = conn.execute("SELECT COUNT(*) FROM abgleich_konflikte").fetchone()[0]
    return {"an": core.get_setting("abgleich_an") == "1",
            "adresse": core.get_setting("abgleich_adresse"),
            "benutzer": core.get_setting("abgleich_benutzer"),
            "name": core.get_setting("abgleich_name"),
            "zuletzt": int(core.get_setting("abgleich_zuletzt") or 0),
            "fehler": core.get_setting("abgleich_fehler"),
            "weg": core.get_setting("abgleich_weg"),
            # Unterwegs über Connect: gekoppelt, oder warum nicht.
            "unterwegs": UNTERWEGS is not None and UNTERWEGS.gekoppelt(),
            "unterwegs_grund": core.get_setting("abgleich_unterwegs_grund"),
            "konflikte": konflikte}


@router.post("/api/abgleich/verbinden")
def abgleich_verbinden(body: Verbinden, user: dict = Depends(_admin())):
    """Bei der SE zu Hause anmelden – wie in der App mit Benutzer und
    Passwort, bei Zwei-Faktor in zwei Schritten. Gespeichert wird nur das
    Token, nie das Passwort."""
    _nur_desktop()
    if core.get_setting("ordner_an") == "1":
        raise HTTPException(400, "Erst den Abgleich über den Cloud-Ordner ausschalten")
    adresse = body.adresse.strip().rstrip("/")
    if not adresse.startswith(("http://", "https://")):
        adresse = "http://" + adresse
    gegen = HttpGegenstelle(adresse)
    try:
        if body.code and body.challenge:
            antwort = gegen.post("/api/login/2fa", {"challenge": body.challenge,
                                                    "code": body.code})
        else:
            antwort = gegen.post("/api/login", {"username": body.benutzer,
                                                "password": body.passwort})
    except AbgleichFehler as e:
        raise HTTPException(400, str(e))
    if antwort.get("totp_required"):
        return {"totp_required": True, "challenge": antwort.get("challenge")}
    gegen.token = antwort.get("token", "")
    try:
        gegen.get("/api/sync/info")
    except AbgleichFehler as e:
        raise HTTPException(400, str(e))
    core.set_setting("abgleich_adresse", adresse)
    core.set_setting("abgleich_benutzer", body.benutzer)
    core.set_setting("abgleich_token", gegen.token)
    core.set_setting("abgleich_an", "1")
    _unterwegs_koppeln(gegen)
    try:
        ergebnis = abgleichen(gegen)
    except AbgleichFehler as e:
        core.set_setting("abgleich_fehler", str(e))
        raise HTTPException(400, str(e))
    return {"ok": True, **ergebnis}


def _unterwegs_koppeln(gegen) -> None:
    """Gleich beim Verbinden zu Hause auch für unterwegs koppeln, mit dem
    frischen Token – wie „Jetzt koppeln“ in der früheren Brücke. Ist
    Connect auf der SE aus, geht der Abgleich trotzdem, nur eben zu Hause;
    der Grund steht dann in den Einstellungen."""
    if UNTERWEGS is None:
        return
    try:
        UNTERWEGS.koppeln(gegen.post("/api/connect/koppeln", {})["link"])
        grund = ""
    except AbgleichFehler as e:
        grund = str(e)
    except Exception as e:               # Kopplung soll das Verbinden nie kippen
        grund = f"Koppeln für unterwegs ging nicht: {e}"
    core.set_setting("abgleich_unterwegs_grund", grund[:300])


@router.post("/api/abgleich/jetzt")
def abgleich_jetzt(user: dict = Depends(_admin())):
    _nur_desktop()
    try:
        return abgleichen()
    except AbgleichFehler as e:
        core.set_setting("abgleich_fehler", str(e))
        raise HTTPException(400, str(e))


@router.post("/api/abgleich/trennen")
def abgleich_trennen(user: dict = Depends(_admin())):
    """Abgleich aus. Die Sammlung hier bleibt, wie sie ist."""
    _nur_desktop()
    for n in ("abgleich_an", "abgleich_token", "abgleich_unterwegs_grund",
              "abgleich_name"):
        core.set_setting(n, "")
    if UNTERWEGS is not None:
        UNTERWEGS.vergessen()
    return {"ok": True}
