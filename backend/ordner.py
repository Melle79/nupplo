"""Abgleich über einen eigenen Cloud-Ordner – ohne SE, ohne Server.

Sven hat am 16.10.2026 entschieden, dass sich Geräte nur auf zwei Wegen
verbinden: über einen frei gewählten eigenen Cloud-Ordner (OneDrive,
Dropbox, iCloud Drive, Google Drive …) oder über Connect mit einer SE. Das
hier ist der erste Weg, zuerst in Nupplo für Windows. iPhone, Mac und
später Android sollen dasselbe Format lesen und schreiben; beschrieben ist
es in `docs/ORDNER-ABGLEICH.md`. Wer das eine ändert, ändert das andere.

**Warum kein gemeinsamer Stand in einer Datei.** Cloud-Dienste gleichen
Dateien irgendwann ab, nicht sofort, und zwei Geräte, die dieselbe Datei
umschreiben, ergeben eine „Konfliktkopie“ – einer verliert. Darum schreibt
jedes Gerät nur in seinen eigenen Unterordner, und nur neue Dateien (Pakete),
die es danach nie wieder anfasst. Gelesen wird alles. Damit darf derselbe
Ordner auch zwei Personen mit einer gemeinsamen Sammlung gehören, ohne dass
es dafür eine eigene Funktion braucht (16.10.2026: keine Benutzer und
Rollen ohne SE).

**Warum jedes Gerät dasselbe Ergebnis bekommt.** Ein Paket enthält ganze
Sätze, keine Änderungsschritte. Je Satz (Tabelle + UUID) gilt die Fassung
mit der neuesten Änderungszeit, bei Gleichstand die des Geräts mit der
größeren Kennung. Das hängt nicht davon ab, in welcher Reihenfolge die
Pakete ankommen – anders als bei der SE, die über `base_rev` entscheidet,
gibt es hier niemanden, der eine Reihenfolge vorgibt.

**Dieselbe Figur auf zwei Geräten** (zwei UUIDs, ein Artikel): Es bleibt
der Satz mit der kleineren UUID. Zusammenlegen – Mengen addieren, wie die
App und der Abgleich mit der SE – darf nur das Gerät, das den unterlegenen
Satz angelegt hat („Herkunft“). Täten es alle, die das Paar bemerken, käme
die Menge mehrfach dazu. Die anderen zeigen bis dahin nur den bleibenden.

**Verschlüsselt.** Der Cloud-Dienst soll die Sammlung nicht lesen können
(Datenschutz: die Sammlung verlässt die Installation nicht lesbar). Den
Schlüssel erzeugt das erste Gerät; er geht als Code von Hand auf die
anderen und liegt nie im Ordner – dort steht nur ein Prüfwert, an dem ein
falscher Code auffällt.
"""
import base64
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import abgleich
import ablage as ablage_mod
import core
import desktop
import sync
from ablage import Ablage, AblageFehler

router = APIRouter()

FORMAT = 1
KOPF = b"NUPPLO-PAKET-1\n"
PRUEF_INFO = b"nupplo-ordner-pruef-1"
# Mehr Sätze je Paket machten das erste Paket einer großen Sammlung zu einer
# Datei, die der Cloud-Dienst lange halb hochgeladen herumliegen lässt.
PAKET_MAX = 2000


class OrdnerFehler(Exception):
    pass


# ------------------------------------------------------------ Schlüssel

def code_aus(schluessel: bytes) -> str:
    """32 Byte als Code zum Abtippen: Base32 in Vierergruppen. Base32,
    weil es keine Verwechsler wie 0/O oder l/1 hat und Groß-/Kleinschreibung
    egal ist."""
    roh = base64.b32encode(schluessel).decode().rstrip("=")
    return "-".join(roh[i:i + 4] for i in range(0, len(roh), 4))


def schluessel_aus(code: str) -> bytes:
    roh = re.sub(r"[^A-Z2-7]", "", code.upper().replace("0", "O").replace("1", "I"))
    try:
        schluessel = base64.b32decode(roh + "=" * (-len(roh) % 8))
    except ValueError:
        raise OrdnerFehler("Der Code stimmt nicht")
    if len(schluessel) != 32:
        raise OrdnerFehler("Der Code ist unvollständig")
    return schluessel


def _pruefwert(schluessel: bytes, ordner_id: str) -> str:
    return base64.b64encode(hmac.new(schluessel, PRUEF_INFO + ordner_id.encode(),
                                     hashlib.sha256).digest()).decode()


# ------------------------------------------------------------ Ordner

def wurzel(pfad: str) -> Ablage:
    """Die Ablage zu einem Ort. `google:` heißt Google Drive (25.11.2026), alles andere ist ein Ordner auf der Platte."""
    if pfad == GOOGLE:
        import google_konto
        return ablage_mod.GoogleDrive(google_konto.sitzung())
    return ablage_mod.Platte(pfad)


GOOGLE = "google:"


def ordner_lesen(w: Ablage) -> dict | None:
    try:
        roh = w.kopf()
    except AblageFehler as e:
        raise OrdnerFehler(str(e))
    if roh is None:
        return None
    try:
        kopf = json.loads(roh.decode("utf-8"))
    except (OSError, ValueError):
        raise OrdnerFehler("ordner.json ist nicht lesbar – vielleicht noch nicht ganz übertragen")
    if kopf.get("format") != FORMAT:
        raise OrdnerFehler("Dieser Ordner stammt von einer neueren Nupplo-Fassung")
    return kopf


def ordner_anlegen(w: Ablage) -> tuple[dict, bytes]:
    schluessel = secrets.token_bytes(32)
    kopf = {"format": FORMAT, "id": secrets.token_hex(8),
            "erstellt": int(time.time() * 1000)}
    kopf["pruef"] = _pruefwert(schluessel, kopf["id"])
    if not w.kopf_anlegen(json.dumps(kopf).encode("utf-8")):
        raise OrdnerFehler("In diesem Ordner gibt es schon einen Abgleich – bitte beitreten")
    return kopf, schluessel


def _verschluesseln(schluessel, ordner_id, geraet, nummer, inhalt: dict) -> bytes:
    nonce = secrets.token_bytes(12)
    aad = f"{ordner_id}/{geraet}/{nummer}".encode()
    roh = gzip.compress(json.dumps(inhalt, ensure_ascii=False, separators=(",", ":")).encode())
    return KOPF + nonce + AESGCM(schluessel).encrypt(nonce, roh, aad)


def _entschluesseln(schluessel, ordner_id, geraet, nummer, daten: bytes) -> dict:
    """Wirft bei allem, was nicht genau so geschrieben wurde – auch bei einer
    Datei, die der Cloud-Dienst erst halb heruntergeladen hat. Die gebundenen
    Daten (Ordner, Gerät, Nummer) verhindern, dass jemand mit Schreibrecht
    im Ordner ein Paket umbenennt oder in einen anderen Unterordner legt."""
    if not daten.startswith(KOPF) or len(daten) < len(KOPF) + 28:
        raise OrdnerFehler("Paket unvollständig")
    nonce = daten[len(KOPF):len(KOPF) + 12]
    aad = f"{ordner_id}/{geraet}/{nummer}".encode()
    roh = AESGCM(schluessel).decrypt(nonce, daten[len(KOPF) + 12:], aad)
    return json.loads(gzip.decompress(roh))


# ------------------------------------------------------------ Merker

def migrieren(conn) -> None:
    # zeit/geraet: welche Fassung des Satzes hier gilt. herkunft: welches
    # Gerät ihn angelegt hat (nur das legt Doppelte zusammen). lokal_rev:
    # der Stand, den er hier durch den Abgleich bekam – das eigene Echo.
    # versteckt: ein Doppelter, der hier nicht gezeigt wird.
    conn.execute("CREATE TABLE IF NOT EXISTS ordner_saetze ("
                 "tabelle TEXT NOT NULL, uuid TEXT NOT NULL, zeit INTEGER, geraet TEXT, "
                 "herkunft TEXT, lokal_rev INTEGER, versteckt INTEGER NOT NULL DEFAULT 0, "
                 "PRIMARY KEY (tabelle, uuid))")
    # Kinder, deren Elternsatz noch fehlt: im nächsten Lauf wieder versuchen.
    # Beim SE-Abgleich kommt der Elternsatz im selben Holen; hier kann sein
    # Paket noch unterwegs sein.
    conn.execute("CREATE TABLE IF NOT EXISTS ordner_warten ("
                 "tabelle TEXT NOT NULL, uuid TEXT NOT NULL, satz TEXT NOT NULL, "
                 "PRIMARY KEY (tabelle, uuid))")
    abgleich.migrieren(conn)


def _merker(conn, t, u):
    return conn.execute("SELECT * FROM ordner_saetze WHERE tabelle = ? AND uuid = ?",
                        (t, u)).fetchone()


def _merken(conn, t, u, **werte):
    alt = _merker(conn, t, u)
    neu = dict(alt) if alt else {"tabelle": t, "uuid": u, "zeit": None, "geraet": None,
                                 "herkunft": None, "lokal_rev": None, "versteckt": 0}
    neu.update(werte)
    conn.execute("INSERT OR REPLACE INTO ordner_saetze (tabelle, uuid, zeit, geraet, herkunft, "
                 "lokal_rev, versteckt) VALUES (:tabelle, :uuid, :zeit, :geraet, :herkunft, "
                 ":lokal_rev, :versteckt)", neu)


def _setting(conn, name, vorgabe=""):
    return abgleich._setting(conn, name, vorgabe)


def _setzen(conn, name, wert):
    abgleich._setzen(conn, name, wert)


def _jetzt_ms() -> int:
    return int(time.time() * 1000)


def _stempel(s: dict) -> tuple:
    return (int(s.get("updated_at") or 0), s.get("_geraet") or "", int(s.get("_nummer") or 0))


# ------------------------------------------------------------ Lesen

def lesen(w: Ablage, schluessel: bytes, ordner_id: str, ich: str, gelesen: dict):
    """Neue Pakete der anderen Geräte, je Gerät lückenlos ab dem gemerkten
    Stand. Eine Lücke oder ein unlesbares Paket heißt: noch nicht ganz
    angekommen. Dann hört das Lesen für dieses Gerät dort auf und versucht
    es im nächsten Lauf wieder – sonst ginge ein Paket verloren."""
    saetze, neu = [], dict(gelesen)
    for d in [g for g in w.geraete() if g != ich]:
        n = int(gelesen.get(d, 0))
        for k in w.pakete(d):
            if k <= n:
                continue
            if k != n + 1:
                break
            try:
                inhalt = _entschluesseln(schluessel, ordner_id, d, k, w.paket(d, k))
            except AblageFehler:
                raise
            except Exception:
                break
            for s in inhalt.get("saetze", []):
                if isinstance(s, dict) and s.get("table") and s.get("uuid"):
                    saetze.append({**s, "_geraet": d, "_nummer": k})
            n = k
        neu[d] = n
    return saetze, neu


def _beste(saetze: list) -> list:
    """Je Satz nur die neueste Fassung – unabhängig von der Reihenfolge."""
    beste = {}
    for s in saetze:
        k = (s["table"], s["uuid"])
        if k not in beste or _stempel(s) > _stempel(beste[k]):
            beste[k] = s
    reihe = list(sync.TABELLEN)
    rang = {t: i for i, t in enumerate(reihe)}
    da = [s for s in beste.values() if not s.get("deleted") and s["table"] in rang]
    weg = [s for s in beste.values() if s.get("deleted") and s["table"] in rang]
    # Eltern zuerst anlegen, Kinder zuerst löschen.
    da.sort(key=lambda s: rang[s["table"]])
    weg.sort(key=lambda s: -rang[s["table"]])
    return da + weg


# ------------------------------------------------------------ Anwenden

class _Lauf:
    def __init__(self, conn, ich, vor, geschickt):
        self.conn = conn
        self.ich = ich
        self.vor = vor
        self.geschickt = geschickt
        # Was hier vor diesem Lauf geändert und noch nicht hinaus ist. Nur
        # das zählt als „hier geändert“ – nicht, was der Lauf selbst eben
        # nebenbei änderte: Die Löschung eines Kaufpostens zieht die Menge
        # des Eintrags nach, und die nachgezogene Menge hielt sonst die
        # Löschung des Eintrags selbst auf (gefunden im Test 06.11.2026).
        self.offen = _offen_hier(conn, geschickt)
        self.karten = sync._Karten(conn)
        self.user = abgleich._benutzer(conn)
        # Was hier beim Zusammenlegen neu entsteht, muss hinaus – alles
        # andere, was das Anwenden ändert, ist Echo.
        self.raus = set()

    def anwenden(self, s: dict) -> bool:
        conn, t, u = self.conn, s["table"], s["uuid"]
        m = _merker(conn, t, u)
        zeit = int(s.get("updated_at") or 0)
        stempel = (zeit, s["_geraet"])
        if m and m["zeit"] is not None and (m["zeit"], m["geraet"] or "") >= stempel:
            return False                         # kennen wir, oder älter
        row = conn.execute(f"SELECT * FROM {t} WHERE uuid = ?", (u,)).fetchone()
        geaendert_hier = row is not None and (t, u) in self.offen
        if geaendert_hier and ((row["updated_at"] or 0), self.ich) > stempel:
            return False                         # hier neuer: geht hinaus
        herkunft = s.get("herkunft") or s["_geraet"]

        if s.get("deleted"):
            if row is not None:
                sync._loeschen(conn, t, row, zeit)
            stein = conn.execute("SELECT rev FROM sync_tombstones WHERE tabelle = ? AND uuid = ?",
                                 (t, u)).fetchone()
            _merken(conn, t, u, zeit=zeit, geraet=s["_geraet"], herkunft=herkunft,
                    lokal_rev=stein[0] if stein else None, versteckt=0)
            return row is not None

        if row is None:
            stein = conn.execute("SELECT rev, deleted_at FROM sync_tombstones "
                                 "WHERE tabelle = ? AND uuid = ?", (t, u)).fetchone()
            # Hier gelöscht (vor diesem Lauf), noch nicht hinaus, und später
            # als die Fassung dort.
            if stein and self.geschickt < stein["rev"] <= self.vor \
                    and (m is None or m["lokal_rev"] != stein["rev"]) \
                    and ((stein["deleted_at"] or 0), self.ich) > stempel:
                return False

        werte = sync._eingang(conn, t, s.get("fields") or {}, self.karten, self.user, True)
        schluessel = sync.TABELLEN[t].get("schluessel")
        if row is None and schluessel:
            kv = [werte.get(k) for k in schluessel]
            if None not in kv:
                andere = conn.execute(
                    f"SELECT * FROM {t} WHERE " + " AND ".join(f"{k} = ?" for k in schluessel)
                    + " AND uuid <> ?", (*kv, u)).fetchone()
                if andere is not None:
                    return self._doppelt(t, andere, s, werte, herkunft)

        self._schreiben(t, row, u, werte, zeit)
        jetzt = conn.execute(f"SELECT rev FROM {t} WHERE uuid = ?", (u,)).fetchone()[0]
        _merken(conn, t, u, zeit=zeit, geraet=s["_geraet"], herkunft=herkunft,
                lokal_rev=jetzt, versteckt=0)
        return True

    def _schreiben(self, t, row, u, werte, zeit, wo_id=None):
        if row is not None or wo_id is not None:
            spalten = ", ".join(f"{k} = ?" for k in werte)
            bed, arg = ("id = ?", wo_id) if wo_id is not None else ("uuid = ?", u)
            self.conn.execute(f"UPDATE {t} SET uuid = ?, {spalten + ', ' if spalten else ''}"
                              f"updated_at = ? WHERE {bed}", (u, *werte.values(), zeit, arg))
        else:
            namen = ["uuid", *werte, "updated_at"]
            self.conn.execute(f"INSERT INTO {t} ({', '.join(namen)}) VALUES "
                              f"({', '.join('?' * len(namen))})", (u, *werte.values(), zeit))

    def _doppelt(self, t, andere, s, werte, herkunft) -> bool:
        """Derselbe Artikel unter zwei UUIDs. Die kleinere bleibt; nur der
        Urheber der größeren legt zusammen (siehe oben)."""
        conn, u, l = self.conn, s["uuid"], andere["uuid"]
        ml = _merker(conn, t, l)
        herkunft_l = (ml["herkunft"] if ml and ml["herkunft"] else self.ich)
        zeit = int(s.get("updated_at") or 0)
        if l < u:
            # Der hier bleibt. Den anderen legt sein Urheber zusammen.
            _merken(conn, t, u, zeit=zeit, geraet=s["_geraet"], herkunft=herkunft,
                    lokal_rev=None, versteckt=1)
            return False
        if herkunft_l == self.ich:
            neu = dict(werte)
            if t == "collection":
                neu["quantity"] = min(9999, (andere["quantity"] or 0) + (werte.get("quantity") or 0))
            # Die Summe ist eine neue Änderung: jetzt, damit sie überall gilt.
            self._schreiben(t, None, u, neu, _jetzt_ms(), wo_id=andere["id"])
            self.raus.add((t, u))
            _merken(conn, t, u, zeit=zeit, geraet=s["_geraet"], herkunft=herkunft,
                    lokal_rev=None, versteckt=0)
            if ml is not None:
                # Die alte UUID kannten andere schon: dort löschen.
                rev = sync._naechster_stand(conn)
                conn.execute("INSERT OR REPLACE INTO sync_tombstones (tabelle, uuid, rev, "
                             "deleted_at) VALUES (?, ?, ?, ?)", (t, l, rev, _jetzt_ms()))
                self.raus.add((t, l))
            # Kinder zeigen jetzt auf die neue UUID – neu hinaus damit.
            for kt, spalte in (("purchases", "entry_id"), ("shopping_items", "recv_entry_id")):
                if t != "collection":
                    break
                for (ku,) in conn.execute(f"SELECT uuid FROM {kt} WHERE {spalte} = ?",
                                          (andere["id"],)).fetchall():
                    conn.execute(f"UPDATE {kt} SET {spalte} = {spalte} WHERE uuid = ?", (ku,))
                    self.raus.add((kt, ku))
            return True
        # Einen fremden Satz legt dessen Urheber zusammen. Hier gilt bis
        # dahin schon der bleibende.
        self._schreiben(t, None, u, werte, zeit, wo_id=andere["id"])
        jetzt = conn.execute(f"SELECT rev FROM {t} WHERE uuid = ?", (u,)).fetchone()[0]
        _merken(conn, t, u, zeit=zeit, geraet=s["_geraet"], herkunft=herkunft,
                lokal_rev=jetzt, versteckt=0)
        _merken(conn, t, l, versteckt=1)
        return True


def _offen_hier(conn, geschickt: int) -> set:
    """Was hier geändert und noch nicht hinaus ist. Alles bis `geschickt`
    ist draußen oder Echo, also reicht der Blick auf die neueren Stände."""
    offen = set()
    for t in sync.TABELLEN:
        merker = {u: l for u, l in conn.execute(
            "SELECT uuid, lokal_rev FROM ordner_saetze WHERE tabelle = ?", (t,))}
        for u, rev in conn.execute(f"SELECT uuid, rev FROM {t} WHERE rev > ?", (geschickt,)):
            if merker.get(u, -1) != rev:
                offen.add((t, u))
    return offen


def _echo_merken(conn, vor: int, raus: set) -> None:
    """Was das Anwenden nebenbei änderte – Kinder einer Löschung, die
    nachgezogene Summe eines Eintrags –, hat das Gerät dort genauso getan
    und selbst hinausgeschickt. Hier ist es Echo. Nicht dazu gehört, was
    `raus` nennt (Zusammengelegtes), und nichts, was der Ordner noch nie
    gesehen hat oder was hier schon vorher offen war: Das muss hinaus."""
    for t in sync.TABELLEN:
        for u, rev in conn.execute(f"SELECT uuid, rev FROM {t} WHERE rev > ?",
                                   (vor,)).fetchall():
            if (t, u) not in raus and _merker(conn, t, u) is not None:
                _merken(conn, t, u, lokal_rev=rev)
        for u, rev in conn.execute("SELECT uuid, rev FROM sync_tombstones WHERE tabelle = ? "
                                   "AND rev > ?", (t, vor)).fetchall():
            if (t, u) not in raus:
                _merken(conn, t, u, lokal_rev=rev)


def holen(conn, saetze: list, neu_gelesen: dict, ich: str) -> int:
    for t, u, satz in conn.execute("SELECT tabelle, uuid, satz FROM ordner_warten").fetchall():
        saetze.append(json.loads(satz))
    conn.execute("DELETE FROM ordner_warten")
    vor = abgleich._stand(conn)
    geschickt = int(_setting(conn, "ordner_geschickt", "0") or 0)
    lauf, anzahl = _Lauf(conn, ich, vor, geschickt), 0
    for s in _beste(saetze):
        try:
            anzahl += lauf.anwenden(s)
        except sync._Abgelehnt as e:
            if e.grund == "orphan":
                conn.execute("INSERT OR REPLACE INTO ordner_warten (tabelle, uuid, satz) "
                             "VALUES (?, ?, ?)", (s["table"], s["uuid"], json.dumps(s)))
            else:
                abgleich._konflikt(conn, s["table"], s["uuid"], e.grund, e.meldung, s)
    _echo_merken(conn, vor, lauf.raus | lauf.offen)
    _setzen(conn, "ordner_gelesen", json.dumps(neu_gelesen, sort_keys=True))
    return anzahl


# ------------------------------------------------------------ Schicken

def offene(conn, ich: str, geschickt: int) -> list:
    karten = sync._Karten(conn)
    saetze, loeschen = [], []
    for t in sync.TABELLEN:
        merker = {r["uuid"]: r for r in conn.execute(
            "SELECT * FROM ordner_saetze WHERE tabelle = ?", (t,))}
        for row in conn.execute(f"SELECT * FROM {t} WHERE rev > ? ORDER BY rev", (geschickt,)):
            m = merker.get(row["uuid"])
            if m is not None and m["lokal_rev"] == row["rev"]:
                continue
            s = sync._satz(t, row, karten, True)
            herkunft = (m["herkunft"] if m is not None and m["herkunft"] else ich)
            saetze.append({"table": t, "uuid": row["uuid"], "updated_at": s["updated_at"],
                           "herkunft": herkunft, "fields": s["fields"]})
            _merken(conn, t, row["uuid"], zeit=s["updated_at"], geraet=ich,
                    herkunft=herkunft, lokal_rev=row["rev"], versteckt=0)
        for st in conn.execute("SELECT uuid, rev, deleted_at FROM sync_tombstones "
                               "WHERE tabelle = ? AND rev > ? ORDER BY rev", (t, geschickt)):
            m = merker.get(st["uuid"])
            # Nie im Ordner gewesen: dort gibt es nichts zu löschen.
            if m is None or m["lokal_rev"] == st["rev"]:
                continue
            loeschen.append({"table": t, "uuid": st["uuid"], "updated_at": st["deleted_at"],
                             "herkunft": m["herkunft"] or ich, "deleted": True})
            _merken(conn, t, st["uuid"], zeit=st["deleted_at"], geraet=ich,
                    lokal_rev=st["rev"], versteckt=0)
    return saetze + loeschen


def _naechste_nummer(w: Ablage, ich: str, gemerkt: int) -> int:
    return max([gemerkt, *w.pakete(ich)]) + 1


def schicken(conn, w, schluessel, kopf, ich) -> int:
    geschickt = int(_setting(conn, "ordner_geschickt", "0") or 0)
    bis = abgleich._stand(conn)
    if bis == geschickt:
        return 0
    saetze = offene(conn, ich, geschickt)
    nummer = int(_setting(conn, "ordner_nummer", "0") or 0)
    for i in range(0, len(saetze), PAKET_MAX):
        nummer = _naechste_nummer(w, ich, nummer)
        daten = _verschluesseln(schluessel, kopf["id"], ich, nummer, {
            "format": FORMAT, "geraet": ich, "nummer": nummer, "zeit": _jetzt_ms(),
            "saetze": saetze[i:i + PAKET_MAX]})
        w.paket_schreiben(ich, nummer, daten)
    # Die Datei liegt, bevor die Merker gelten (Commit am Ende von core.db):
    # Fällt dazwischen der Strom aus, geht dasselbe noch einmal hinaus –
    # doppelt schadet nicht, verloren schon.
    _setzen(conn, "ordner_nummer", nummer)
    _setzen(conn, "ordner_geschickt", bis)
    return len(saetze)


# ------------------------------------------------------------ Lauf

_sperre = threading.Lock()


def _zugang(conn):
    pfad = _setting(conn, "ordner_pfad")
    schluessel = _setting(conn, "ordner_schluessel")
    if not pfad or not schluessel:
        raise OrdnerFehler("Kein Cloud-Ordner eingerichtet")
    w = wurzel(pfad)
    try:
        da = w.da()
    except AblageFehler as e:
        raise OrdnerFehler(str(e))
    if not da:
        raise OrdnerFehler("Der Cloud-Ordner ist gerade nicht da")
    kopf = ordner_lesen(w)
    if kopf is None:
        raise OrdnerFehler("Im Cloud-Ordner fehlt ordner.json")
    schluessel = base64.b64decode(schluessel)
    if kopf["id"] != _setting(conn, "ordner_id") or not hmac.compare_digest(
            kopf.get("pruef", ""), _pruefwert(schluessel, kopf["id"])):
        raise OrdnerFehler("Im Cloud-Ordner liegt jetzt ein anderer Abgleich")
    return w, schluessel, kopf


def abgleichen() -> dict:
    """Einmal lesen und schreiben. Läuft nie zweimal gleichzeitig."""
    with _sperre:
        with core.db() as conn:
            migrieren(conn)
            w, schluessel, kopf = _zugang(conn)
            try:
                return _abgleichen(conn, w, schluessel, kopf, _setting(conn, "ordner_geraet"))
            except AblageFehler as e:
                # Google Drive mitten im Lauf weg: Die Transaktion fällt
                # zurück, die Merker bleiben, der nächste Lauf holt alles nach.
                raise OrdnerFehler(str(e))


def _abgleichen(conn, w: Ablage, schluessel, kopf, ich) -> dict:
    # Der eigene Unterordner gleich zu Anfang, auch ohne Paket: Sonst
    # zeigte ein neu angelegter Abgleich mit leerer Sammlung „Geräte
    # im Ordner: 0“ (gesehen 06.11.2026).
    w.geraet_anlegen(ich)
    # Lesen vor dem Sperren: Bei OneDrive mit „Dateien bei Bedarf“ und bei
    # Google Drive lädt erst das Lesen ein Paket herunter, und so lange
    # soll die Web-App nicht auf die Datenbank warten müssen.
    gelesen = json.loads(_setting(conn, "ordner_gelesen", "{}") or "{}")
    saetze, neu_gelesen = lesen(w, schluessel, kopf["id"], ich, gelesen)
    # Dann sofort sperren: Zwischen dem Merken des Stands und dem ersten
    # Schreiben dürfte sonst eine Änderung aus der Web-App fallen – sie
    # gälte als Echo und ginge nie hinaus.
    conn.execute("BEGIN IMMEDIATE")
    geholt = holen(conn, saetze, neu_gelesen, ich)
    geschickt = schicken(conn, w, schluessel, kopf, ich)
    _setzen(conn, "ordner_zuletzt", int(time.time()))
    _setzen(conn, "ordner_fehler", "")
    return {"geholt": geholt, "geschickt": geschickt, "abgelehnt": 0}


def _alles_hier_loeschen(conn) -> None:
    """„Durch den Ordner ersetzen“: Die Sammlung hier verschwindet, ohne dass
    eine Löschung in den Ordner geht – dort soll ja alles bleiben."""
    for t in reversed(list(sync.TABELLEN)):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM sync_tombstones")
    conn.execute("DELETE FROM ordner_saetze")
    _setzen(conn, "ordner_geschickt", abgleich._stand(conn))


def einrichten(pfad: str, code: str = "", ersetzen: bool = False) -> dict:
    """Anlegen (ohne Code) oder beitreten (mit Code)."""
    w = _gewaehlt(pfad)
    kopf = ordner_lesen(w)
    if kopf is None:
        if code:
            raise OrdnerFehler("In diesem Ordner gibt es noch keinen Abgleich")
        kopf, schluessel = ordner_anlegen(w)
    else:
        if not code:
            raise OrdnerFehler("In diesem Ordner gibt es schon einen Abgleich – bitte den Code eingeben")
        schluessel = schluessel_aus(code)
        if not hmac.compare_digest(kopf.get("pruef", ""), _pruefwert(schluessel, kopf["id"])):
            raise OrdnerFehler("Der Code passt nicht zu diesem Ordner")
    with core.db() as conn:
        migrieren(conn)
        conn.execute("DELETE FROM ordner_saetze")
        conn.execute("DELETE FROM ordner_warten")
        if ersetzen:
            _alles_hier_loeschen(conn)
        else:
            _setzen(conn, "ordner_geschickt", "0")
        _setzen(conn, "ordner_pfad", pfad if pfad == GOOGLE else str(Path(pfad).expanduser()))
        _setzen(conn, "ordner_id", kopf["id"])
        _setzen(conn, "ordner_schluessel", base64.b64encode(schluessel).decode())
        # Je Einrichtung eine neue Kennung: Ein neu beigetretenes Gerät soll
        # nicht weiterzählen, wo ein früheres mit derselben Kennung aufhörte.
        _setzen(conn, "ordner_geraet", secrets.token_hex(8))
        _setzen(conn, "ordner_nummer", "0")
        _setzen(conn, "ordner_gelesen", "{}")
        _setzen(conn, "ordner_an", "1")
    return abgleichen()


def trennen() -> None:
    """Abgleich aus. Die Sammlung hier und der Ordner bleiben, wie sie sind."""
    for n in ("ordner_an", "ordner_schluessel", "ordner_fehler"):
        core.set_setting(n, "")


def geraete_im_ordner(w: Ablage) -> int:
    try:
        return len(w.geraete())
    except (OSError, AblageFehler):
        return 0


def _gewaehlt(pfad: str) -> Ablage:
    """Die Ablage zum Einrichten. Auf der Platte muss es den gewählten
    Ordner geben; bei Google muss man angemeldet sein."""
    if pfad != GOOGLE and not Path(pfad).expanduser().is_dir():
        raise OrdnerFehler("Diesen Ordner gibt es nicht")
    try:
        return wurzel(pfad)
    except AblageFehler as e:
        raise OrdnerFehler(str(e))


# ------------------------------------------------------------ Endpunkte

class Einrichten(BaseModel):
    pfad: str = Field(min_length=1, max_length=1000)
    code: str = Field(default="", max_length=200)
    ersetzen: bool = False


def _fehler(e: Exception):
    core.set_setting("ordner_fehler", str(e)[:300])
    return HTTPException(400, str(e))


@router.get("/api/ordner")
def ordner_stand(user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    pfad = core.get_setting("ordner_pfad")
    an = core.get_setting("ordner_an") == "1"
    import google_konto
    # Bei Google kostet das Zählen Anfragen ans Netz und scheitert ohne
    # Anmeldung – dann lieber 0 als ein Fehler beim Öffnen der Einstellungen.
    geraete = 0
    if pfad and (pfad != GOOGLE or (an and google_konto.verbunden())):
        try:
            geraete = geraete_im_ordner(wurzel(pfad))
        except AblageFehler:
            pass
    return {"an": an, "pfad": pfad, "google": pfad == GOOGLE,
            "geraete": geraete,
            "zuletzt": int(core.get_setting("ordner_zuletzt") or 0),
            "fehler": core.get_setting("ordner_fehler")}


def vorschlaege() -> list[dict]:
    """Cloud-Ordner, die es auf diesem PC schon gibt – zum Antippen statt
    Abtippen. iCloud Drive zuerst: Darüber gleicht Windows mit iPhone und
    Mac ab (25.11.2026), und „iCloud für Windows“ legt ihn unter
    `%USERPROFILE%\\iCloudDrive` an; die Fassung aus dem Microsoft Store
    ebenso."""
    heim = Path(os.environ.get("USERPROFILE") or Path.home())
    kandidaten = [("iCloud Drive", heim / "iCloudDrive")]
    for var in ("OneDrive", "OneDriveConsumer"):
        if os.environ.get(var):
            kandidaten.append(("OneDrive", Path(os.environ[var])))
    kandidaten += [("OneDrive", heim / "OneDrive"), ("Dropbox", heim / "Dropbox")]
    gesehen, aus = set(), []
    for name, pfad in kandidaten:
        try:
            echt = str(pfad.resolve())
            if echt in gesehen or not pfad.is_dir():
                continue
        except OSError:
            continue
        gesehen.add(echt)
        aus.append({"name": name, "pfad": str(pfad)})
    return aus


@router.get("/api/ordner/vorschlaege")
def ordner_vorschlaege(user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    return {"ordner": vorschlaege()}


@router.get("/api/ordner/pruefen")
def ordner_pruefen(pfad: str, user: dict = Depends(abgleich._admin())):
    """Vor dem Einrichten: anlegen oder beitreten? Und liegt hier schon eine
    Sammlung, nach der man fragen muss?"""
    abgleich._nur_desktop()
    try:
        w = _gewaehlt(pfad)
        vorhanden = ordner_lesen(w) is not None
    except OrdnerFehler as e:
        raise HTTPException(400, str(e))
    with core.db() as conn:
        hier = conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0] \
            + conn.execute("SELECT COUNT(*) FROM wanted").fetchone()[0]
    return {"vorhanden": vorhanden, "geraete": geraete_im_ordner(w), "hier_leer": hier == 0}


@router.post("/api/ordner/einrichten")
def ordner_einrichten(body: Einrichten, user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    if core.get_setting("abgleich_an") == "1":
        raise HTTPException(400, "Erst den Abgleich mit der Nupplo SE ausschalten")
    try:
        ergebnis = einrichten(body.pfad.strip(), body.code.strip(), body.ersetzen)
    except OrdnerFehler as e:
        raise _fehler(e)
    return {"ok": True, **ergebnis}


@router.get("/api/ordner/code")
def ordner_code(user: dict = Depends(abgleich._admin())):
    """Den Code noch einmal zeigen – für das nächste Gerät."""
    abgleich._nur_desktop()
    roh = core.get_setting("ordner_schluessel")
    if not roh:
        raise HTTPException(404, "Kein Cloud-Ordner eingerichtet")
    return {"code": code_aus(base64.b64decode(roh))}


def qr_inhalt(code: str, ordner_id: str) -> str:
    """Was der QR-Code trägt: Code und Kennung des Ordners. Mit der Kennung
    kann ein Telefon vor dem Beitreten prüfen, ob es den richtigen Ordner
    gewählt hat. Ohne Bindestriche, weil QR-Codes Großbuchstaben und Ziffern
    am dichtesten packen."""
    return f"NUPPLO-ORDNER:{code.replace('-', '')}:{ordner_id.upper()}"


@router.get("/api/ordner/qr")
def ordner_qr(user: dict = Depends(abgleich._admin())):
    """Den Code als QR-Code – iPhone und Android sollen ihn vom Bildschirm
    abscannen, statt 52 Zeichen abzutippen."""
    abgleich._nur_desktop()
    roh = core.get_setting("ordner_schluessel")
    if not roh:
        raise HTTPException(404, "Kein Cloud-Ordner eingerichtet")
    import io

    import segno
    puffer = io.BytesIO()
    # Wie beim Zwei-Faktor-Code: viewBox statt fester Größe, weißer Grund.
    segno.make(qr_inhalt(code_aus(base64.b64decode(roh)), core.get_setting("ordner_id")),
               error="m").save(puffer, kind="svg", scale=5, border=4,
                               omitsize=True, light="#fff")
    return Response(puffer.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})


@router.post("/api/ordner/jetzt")
def ordner_jetzt(user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    try:
        return abgleichen()
    except OrdnerFehler as e:
        raise _fehler(e)


@router.post("/api/ordner/trennen")
def ordner_trennen(user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    trennen()
    return {"ok": True}


# ------------------------------------------------------------ Google Drive

@router.get("/api/ordner/google")
def google_stand(user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    import google_konto
    return {"eingerichtet": google_konto.eingerichtet(), "verbunden": google_konto.verbunden()}


@router.post("/api/ordner/google/anmelden")
def google_anmelden(request: Request, user: dict = Depends(abgleich._admin())):
    """Öffnet Googles Anmeldung im Browser des PCs. Die App fragt danach
    `GET /api/ordner/google`, bis der Zugang da ist."""
    abgleich._nur_desktop()
    import webbrowser

    import google_konto
    # Zurück an genau diese SE: Für Desktop-Programme lässt Google jede
    # Adresse auf 127.0.0.1 zu, egal welcher Anschluss.
    rueckweg = f"http://127.0.0.1:{request.url.port}/api/ordner/google/zurueck"
    try:
        adresse = google_konto.anmelde_adresse(rueckweg)
    except AblageFehler as e:
        raise HTTPException(400, str(e))
    try:
        webbrowser.open(adresse)
    except Exception:
        pass
    # Auch zurückgeben: Öffnet sich kein Browser, zeigt die App den Link.
    return {"adresse": adresse}


_SEITE = """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nupplo</title><body style="font-family:system-ui,sans-serif;max-width:32rem;
margin:4rem auto;padding:0 1rem;line-height:1.5"><h1 style="font-size:1.4rem">{titel}</h1>
<p>{text}</p></body></html>"""


@router.get("/api/ordner/google/zurueck", include_in_schema=False)
def google_zurueck(state: str = "", code: str = "", error: str = ""):
    """Hierher schickt Google den Browser nach der Anmeldung. Ohne Anmeldung
    bei Nupplo, denn es ist der Browser, nicht die App – geschützt über
    `state`, den nur die App kennt, die die Anmeldung begonnen hat."""
    abgleich._nur_desktop()
    import html

    import google_konto
    if error or not code:
        titel, text = "Nicht verbunden", "Die Anmeldung bei Google wurde abgebrochen. " \
            "Du kannst es in Nupplo noch einmal versuchen."
    else:
        try:
            google_konto.zurueck(state, code)
            titel, text = "Verbunden ✔", "Nupplo darf jetzt seinen Ordner in Google Drive " \
                "benutzen. Du kannst dieses Fenster schließen und zu Nupplo zurückgehen."
        except AblageFehler as e:
            titel, text = "Nicht verbunden", str(e)
    return HTMLResponse(_SEITE.format(titel=html.escape(titel), text=html.escape(text)),
                        headers={"Cache-Control": "no-store"})


@router.post("/api/ordner/google/abmelden")
def google_abmelden(user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    import google_konto
    if core.get_setting("ordner_pfad") == GOOGLE:
        trennen()
    google_konto.abmelden()
    return {"ok": True}
