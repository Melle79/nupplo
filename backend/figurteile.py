"""Teile je Figur, Doppelgänger und „Im Bau" in der Wunschliste (3.9.0).

**Anlass, 09.10.2026.** Zwei Fragen an denselben Daten:

- *Woran erkenne ich sw1423?* Kopf, Torso und Beine sind dieselben wie bei
  sw1422 – nur der Entfernungsmesser am Helm unterscheidet sie. Der
  Steckbrief soll das sagen.
- *Teil für Teil sammeln.* Wer Kopf und Torso einer Figur hat, dem fehlen
  nur die Beine. Die Figur steht dann als „Im Bau 2/3" in der Wunschliste,
  die Teile sind abhakbar.

**Woher die Teile kommen.** Bis 3.8 nur über BrickLink (`subsets`), mit
eigenem Schlüssel. Jetzt aus der Datei `figurteile.ndjson`, die der
Katalogdienst aus Rebrickables freien Daten baut und neben dem Index
ablegt – in BrickLink-Nummern, mit Doppelgängern. Ohne die Datei bleibt
BrickLink der Weg, dann ohne Doppelgänger.

**Die Häkchen** stehen in `wanted.teile_da` (JSON-Liste aus
„Teilenummer|Farbe"). Eine Spalte statt einer Tabelle: Der Abgleich mit der
iOS-App trägt Spalten von selbst (`sync.py` liest sie aus der Tabelle, die
App übernimmt nur, was sie kennt) – ohne neue Protokollfassung.
"""
import json
import os
import re
import time

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import core
import integrations

router = APIRouter()

# Die Datei kommt vom Hub, nicht von GitHub (17.10.2026); siehe
# `hubpreise.katalog_datei`. Eine eigene Adresse geht weiter über die
# Einstellung oder `FIGURTEILE_QUELLE` – so zeigt die Testinstanz auf den
# Katalogdienst im LAN.
# Gemessen am 09.10.2026: 8 MB für 17.289 Figuren. Ein Deckel, weil die
# Adresse sich verstellen lässt.
MAX_BYTES = 48 * 1024 * 1024

ART_NAME = {"kopf": "Kopf", "haare": "Haare", "kopfbedeckung": "Kopfbedeckung",
            "torso": "Oberkörper", "beine": "Beine", "hals": "Halsteil",
            "zubehoer": "Zubehör", "sonst": "Sonstiges"}


def migrieren(conn) -> None:
    conn.executescript("""
CREATE TABLE IF NOT EXISTS figurteile (
    item_no TEXT PRIMARY KEY,
    rb TEXT NOT NULL DEFAULT '',
    teile TEXT NOT NULL,          -- [[nr, farbe, anz], …]
    dg TEXT NOT NULL DEFAULT '[]' -- Doppelgänger, wie in der Datei
);
CREATE TABLE IF NOT EXISTS figurteile_teil (
    nr TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    art TEXT NOT NULL DEFAULT ''
);
""")
    # Das Ding auf Deutsch („Entfernungsmesser"), seit die Prüffrage unter
    # dem Scan-Treffer am 10.10.2026 englisch dastand. Nachgerüstet, weil
    # Testinstanzen die Tabelle schon ohne die Spalte haben.
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(figurteile_teil)")}
    if "name_de" not in spalten:
        conn.execute("ALTER TABLE figurteile_teil ADD COLUMN name_de TEXT NOT NULL DEFAULT ''")
    # Sets der Figur aus Rebrickable (Katalogdienst 1.24.0, 13.10.2026):
    # BrickLink ordnet ganz neue Figuren oft tagelang keinem Set zu.
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(figurteile)")}
    if "sets" not in spalten:
        conn.execute("ALTER TABLE figurteile ADD COLUMN sets TEXT NOT NULL DEFAULT '[]'")


def quelle() -> str:
    """Eine eigens eingestellte Adresse – leer heißt: vom Hub."""
    return (core.get_setting("figurteile_quelle")
            or os.environ.get("FIGURTEILE_QUELLE", "").strip())


def ziehen() -> dict:
    """Die Datei holen, wenn sie sich geändert hat (ETag), und ersetzen.

    Ganz ersetzen statt ergänzen: Die Datei ist ein vollständiger Stand,
    und eine Figur, deren Zuordnung sich als falsch herausgestellt hat,
    muss auch wieder herausfallen.
    """
    if core.get_setting("katalog_aus") == "1":
        return {"geholt": 0, "grund": "abgeschaltet"}
    kopf = {"User-Agent": integrations.USER_AGENT}
    etag = core.get_setting("figurteile_etag")
    if etag:
        kopf["If-None-Match"] = etag
    if quelle():
        r = requests.get(quelle(), headers=kopf, timeout=120)
    else:
        import hubpreise
        r = hubpreise.katalog_datei("figurteile", kopf)
    core.set_setting("figurteile_geprueft_at", str(int(time.time())))
    if r.status_code == 304:
        return {"geholt": 0, "grund": "unverändert"}
    if r.status_code == 404:
        # Noch nicht veröffentlicht – kein Fehler, nur nichts da.
        return {"geholt": 0, "grund": "keine Datei"}
    r.raise_for_status()
    if len(r.content) > MAX_BYTES:
        raise ValueError("Die Teiledatei ist zu groß")
    farben, farben_de, teile, figuren = {}, {}, [], []
    for zeile in r.text.splitlines():
        try:
            d = json.loads(zeile)
        except ValueError:
            continue
        if "farben" in d:
            farben = {str(k): str(v) for k, v in (d["farben"] or {}).items()}
            farben_de = {str(k): str(v) for k, v in (d.get("farben_de") or {}).items()}
        elif "teil" in d:
            teile.append((str(d["teil"])[:60], str(d.get("name") or "")[:300],
                          str(d.get("art") or "")[:20], str(d.get("de") or "")[:80]))
        elif d.get("item_no"):
            # **Nummern klein.** Die Probe führte 43 Figuren doppelt
            # (LLP002/llp002), seit Katalogdienst 1.22.3 nur noch klein. Wir
            # speichern klein und vergleichen ohne Groß/klein – sonst fände
            # eine Sammlung mit „LLP002" nichts mehr (11.10.2026).
            dg, gesehen = [], set()
            for p in d.get("dg") or []:
                k = str(p.get("item_no") or "").lower()
                if not k or k in gesehen or k == str(d["item_no"]).lower():
                    continue
                gesehen.add(k)
                dg.append(dict(p, item_no=k))
            sets = [str(x)[:30] for x in (d.get("sets") or []) if x][:200]
            figuren.append((str(d["item_no"]).lower()[:60], str(d.get("rb") or "")[:20],
                            json.dumps(d.get("teile") or []), json.dumps(dg),
                            json.dumps(sets)))
    if not figuren:
        return {"geholt": 0, "grund": "leer"}
    with core.db() as conn:
        migrieren(conn)
        conn.execute("DELETE FROM figurteile")
        conn.execute("DELETE FROM figurteile_teil")
        conn.executemany("INSERT OR REPLACE INTO figurteile_teil (nr, name, art, name_de)"
                         " VALUES (?, ?, ?, ?)", teile)
        conn.executemany("INSERT OR REPLACE INTO figurteile (item_no, rb, teile, dg, sets)"
                         " VALUES (?, ?, ?, ?, ?)", figuren)
    core.set_setting("figurteile_farben", json.dumps(farben))
    core.set_setting("figurteile_farben_de", json.dumps(farben_de))
    if r.headers.get("ETag"):
        core.set_setting("figurteile_etag", r.headers["ETag"])
    core.set_setting("figurteile_geholt_at", str(int(time.time())))
    return {"geholt": len(figuren)}


# BrickLinks Mehrteiler tragen einen Zusatz „cNN“: 970c09 sind Hüfte und
# Beine, die Beine in Farbe 9 (Hellgrau), die Hüfte in der Farbe des
# Eintrags. Bei 973pb0282c01/c03 steht der Zusatz für Arme und Hände.
# Ohne den Zusatz ist es dieselbe Form. Anlass: sw0002 gegen sw0002a
# (16.10.2026) – Beine 970c09 gegen 970c86, Farbe des Eintrags Braun gegen
# Rotbraun (die Hüfte), die Prüffrage sagte „Welche Beine?“ statt der Farbe.
_ZUSATZ = re.compile(r"c(\d+)$")
_BEINE = re.compile(r"^97[0-2]c(\d+)")


def _form(nr, farbe, farben) -> tuple:
    """Form ohne Zusatz und die Farbe, in der man das Teil sieht."""
    nr = str(nr)
    form = _ZUSATZ.sub("", nr) if not nr.startswith("rb:") else nr
    sicht = str(farbe)
    m = _BEINE.match(nr)
    if m and int(m.group(1)):
        sicht = str(int(m.group(1)))
    return form, farben.get(sicht, ""), farben.get("de", {}).get(sicht, "")


def _teil(nr, farbe, anz, info, farben) -> dict:
    name, art, name_de = info.get(nr, ("", "", ""))
    bl = not str(nr).startswith("rb:") and isinstance(farbe, int)
    form, sicht, sicht_de = _form(nr, farbe, farben)
    return {
        "form": form, "form_farbe": sicht, "form_farbe_de": sicht_de,
        "key": "%s|%s" % (nr, farbe),
        "item_id": nr, "item_type": "part", "name": name, "art": art,
        "art_name": ART_NAME.get(art, ""),
        "name_de": name_de,
        "color_id": farbe, "color_name": farben.get(str(farbe), ""),
        "color_name_de": farben.get("de", {}).get(str(farbe), ""),
        "qty": anz,
        # Rebrickable-Nummern haben bei BrickLink kein Bild.
        "img_url": ("https://img.bricklink.com/ItemImage/PN/%s/%s.png"
                    % (farbe, requests.utils.quote(nr))) if bl else "",
        "bricklink_url": ("https://www.bricklink.com/v2/catalog/catalogitem.page?P="
                          + requests.utils.quote(nr)) if bl else "",
    }


def _info(conn, nummern) -> dict:
    nummern = list(nummern)
    if not nummern:
        return {}
    marken = ",".join("?" * len(nummern))
    return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        f"SELECT nr, name, art, name_de FROM figurteile_teil WHERE nr IN ({marken})",
        nummern)}


def _farben() -> dict:
    """BrickLink-ID → englischer Name, unter „de" die deutschen."""
    try:
        aus = json.loads(core.get_setting("figurteile_farben") or "{}")
        aus["de"] = json.loads(core.get_setting("figurteile_farben_de") or "{}")
        return aus
    except ValueError:
        return {"de": {}}


def sets_aus_datei(fig_no: str) -> list:
    """Sets einer Figur aus der Teiledatei, mit Namen – deutsch, wenn der
    Katalog einen deutschen Setnamen kennt. Leer, wenn nichts bekannt ist."""
    try:
        with core.db() as conn:
            row = conn.execute("SELECT sets FROM figurteile WHERE item_no = ?",
                               (str(fig_no).lower(),)).fetchone()
            nummern = json.loads(row["sets"]) if row and row["sets"] else []
            aus = []
            for nr in nummern:
                n = conn.execute("SELECT name FROM set_namen WHERE nr = ? AND sprache = 'de'",
                                 (nr,)).fetchone() if _tabelle(conn, "set_namen") else None
                e = conn.execute("SELECT name FROM set_daten WHERE nr = ?",
                                 (nr,)).fetchone() if _tabelle(conn, "set_daten") else None
                aus.append({"no": nr, "name": (n and n["name"]) or (e and e["name"]) or "",
                            "qty": 1, "quelle": "katalog"})
            return aus
    except Exception:
        return []


def _tabelle(conn, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                             (name,)).fetchone())


def aus_datei(fig_no: str) -> dict | None:
    """Teile und Doppelgänger aus der Datei – oder None, wenn sie fehlt."""
    try:
        with core.db() as conn:
            row = conn.execute("SELECT teile, dg FROM figurteile"
                               " WHERE lower(item_no) = ?",
                               (str(fig_no).lower(),)).fetchone()
            if not row:
                return None
            roh = json.loads(row["teile"])
            dg = json.loads(row["dg"])
            nummern = {t[0] for t in roh}
            for p in dg:
                nummern |= {k.split("|")[0] for k in p.get("hier", []) + p.get("dort", [])}
            info = _info(conn, nummern)
            # Ohne Groß/klein und ohne Doppelte – alte Dateien hatten beides.
            ohne_doppelte, gesehen = [], {str(fig_no).lower()}
            for p in dg:
                k = str(p.get("item_no") or "").lower()
                if k and k not in gesehen:
                    gesehen.add(k)
                    ohne_doppelte.append(p)
            dg = ohne_doppelte
            partner = [str(p["item_no"]).lower() for p in dg]
            namen, schreibweise = {}, {}
            if partner:
                marken = ",".join("?" * len(partner))
                for tab in ("katalog_index", "collection", "wanted"):
                    spalte = "item_no" if tab == "katalog_index" else "item_id"
                    for r in conn.execute(
                            f"SELECT {spalte}, name FROM {tab} WHERE lower({spalte})"
                            f" IN ({marken}) AND name != ''", partner):
                        namen.setdefault(r[0].lower(), r[1])
                        # Wie der Katalog die Nummer schreibt (LLP002a)
                        schreibweise.setdefault(r[0].lower(), r[0])
    except Exception:
        return None             # Tabelle fehlt o. Ä.: dann eben BrickLink
    farben = _farben()
    teile = [_teil(t[0], t[1], t[2] if len(t) > 2 else 1, info, farben) for t in roh]

    def zerlegen(schluessel):
        aus = []
        for k in schluessel:
            nr, _, f = k.partition("|")
            aus.append(_teil(nr, int(f) if f.isdigit() else f, 1, info, farben))
        return aus

    return {"quelle": "katalog", "teile": teile, "doppelgaenger": [
        {"item_no": schreibweise.get(str(p["item_no"]).lower(), p["item_no"]),
         "name": namen.get(str(p["item_no"]).lower(), ""),
         "img_url": integrations.minifig_bild(
             schreibweise.get(str(p["item_no"]).lower(), p["item_no"])),
         "hier": zerlegen(p.get("hier", [])),
         "dort": zerlegen(p.get("dort", [])),
         "verdeckt": bool(p.get("verdeckt"))} for p in dg]}


def anzahl_teile(conn, item_ids) -> dict:
    """Wie viele verschiedene Teile hat jede dieser Figuren? Für „x/y"."""
    item_ids = list(item_ids)
    if not item_ids:
        return {}
    marken = ",".join("?" * len(item_ids))
    aus = {}
    klein = {}
    for i in item_ids:
        klein.setdefault(str(i).lower(), []).append(i)
    try:
        for r in conn.execute(f"SELECT item_no, teile FROM figurteile"
                              f" WHERE lower(item_no) IN ({marken})",
                              [str(i).lower() for i in item_ids]):
            n = len({"%s|%s" % (t[0], t[1]) for t in json.loads(r[1])})
            for original in klein.get(r[0].lower(), [r[0]]):
                aus[original] = n
    except Exception:
        pass
    # Ohne Datei: aus dem BrickLink-Zwischenspeicher, falls schon geholt.
    rest = [i for i in item_ids if i not in aus]
    if rest:
        marken = ",".join("?" * len(rest))
        for r in conn.execute(f"SELECT fig_no, data FROM fig_parts"
                              f" WHERE fig_no IN ({marken})", rest):
            try:
                aus[r[0]] = len({"%s|%s" % (p.get("item_id"), p.get("color_id"))
                                 for p in json.loads(r[1])})
            except ValueError:
                pass
    return aus


def haken_lesen(wert) -> list:
    try:
        liste = json.loads(wert or "[]")
    except ValueError:
        return []
    return [str(x) for x in liste if isinstance(x, str)] if isinstance(liste, list) else []


# ------------------------------------------------------------- Endpunkte

def _benutzer(request: Request) -> dict:
    # Erst beim Aufruf nach `main` greifen: `core.init_db` lädt dieses Modul
    # schon, bevor `main` fertig ist.
    import main
    return main.current_user(request)


def _admin(request: Request) -> dict:
    import main
    return main.admin_user(_benutzer(request))


@router.get("/api/figurteile/{fig_no}")
def figur(fig_no: str, nur_datei: bool = False, user: dict = Depends(_benutzer)):
    """Teile einer Figur mit Art und Häkchen-Schlüssel, und ihre Doppelgänger.

    Ohne Datei antwortet BrickLink (`quelle: "bricklink"`), dann ohne
    Doppelgänger – die rechnet nur der Katalogdienst über alle Figuren.
    `nur_datei`: für den Hinweis unter den Scan-Treffern. Der fragt bis zu
    drei Kandidaten auf einmal und braucht nur die Doppelgänger, die es
    ohne Datei nicht gibt – BrickLink zu fragen, kostete Abrufe für nichts.
    """
    d = _figur(fig_no, nur_datei)
    if not nur_datei:
        # Für „Teil für Teil" in Suche und Steckbrief (3.9.14): Steht die
        # Figur schon auf der Wunschliste, kommen ihre Häkchen mit.
        with core.db() as conn:
            row = _wunsch(conn, fig_no)
        d["auf_wunschliste"] = row is not None
        d["teile_da"] = haken_lesen(row["teile_da"]) if row else []
    return d


def _figur(fig_no: str, nur_datei: bool) -> dict:
    d = aus_datei(fig_no)
    if d is not None:
        return d
    import main
    if nur_datei or fig_no.startswith(("fig-", "manuell-", "custom-")) \
            or not integrations.bricklink_enabled():
        return {"quelle": "", "teile": [], "doppelgaenger": []}
    try:
        teile = main._fill_part_colors(main._fig_parts_cached(fig_no))
    except Exception:
        return {"quelle": "", "teile": [], "doppelgaenger": []}
    for t in teile:
        t["key"] = "%s|%s" % (t.get("item_id"), t.get("color_id"))
        t.setdefault("art", "")
        t.setdefault("art_name", "")
    return {"quelle": "bricklink", "teile": teile, "doppelgaenger": []}


class HakenBody(BaseModel):
    teile_da: list[str] = Field(default_factory=list, max_length=60)


@router.put("/api/wanted/{wanted_id}/teile")
def haken_setzen(wanted_id: int, body: HakenBody, user: dict = Depends(_benutzer)):
    """Welche Teile einer gewünschten Figur schon da sind."""
    haken = sorted({k.strip()[:80] for k in body.teile_da if k.strip()})
    with core.db() as conn:
        row = conn.execute("SELECT item_id FROM wanted WHERE id = ?",
                           (wanted_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Eintrag nicht gefunden")
        # Leer statt „[]": So bleibt eine nie angefasste Figur von einer
        # unterscheidbar, deren Häkchen man alle wieder entfernt hat – und
        # beide sind nicht „im Bau".
        conn.execute("UPDATE wanted SET teile_da = ? WHERE id = ?",
                     (json.dumps(haken) if haken else "", wanted_id))
        gesamt = anzahl_teile(conn, [row["item_id"]]).get(row["item_id"], 0)
    return {"ok": True, "da": len(haken), "gesamt": gesamt}


def _wunsch(conn, fig_no: str):
    return conn.execute("SELECT id, teile_da FROM wanted WHERE item_id = ?"
                        " AND item_type = 'minifig'", (fig_no,)).fetchone()


class HakenAusSucheBody(HakenBody):
    # Was die Wunschliste braucht, falls die Figur dort noch fehlt – wie
    # beim „☆ Merken" im selben Fenster.
    name: str = Field(default="", max_length=300)
    img_url: str = Field(default="", max_length=600)
    bricklink_url: str = Field(default="", max_length=600)
    year: int = Field(default=0, ge=0, le=2100)


@router.put("/api/figurteile/{fig_no}/haken")
def haken_aus_der_suche(fig_no: str, body: HakenAusSucheBody,
                        user: dict = Depends(_benutzer)):
    """Teile abhaken, bevor die Figur auf der Wunschliste steht (3.9.14).

    **Anlass, 11.10.2026.** Das Puzzle ging nur über die Wunschliste: Figur
    suchen, merken, zur Wunschliste wechseln, dort aufklappen. Sven: „können
    wir das nicht auch in der Suche unterbringen … wandert es automatisch in
    die Wunschliste?" Der erste Haken setzt die Figur deshalb auf die
    Wunschliste. Das ist kein stilles Ändern: Wer abhakt, sammelt sie.

    Alle Haken wieder weg nimmt sie **nicht** von der Liste. Ein Wunsch, den
    man vielleicht schon mit Notizen oder auf einer Einkaufsliste hat,
    verschwände sonst durch ein Häkchen.
    """
    import main
    neu = False
    with core.db() as conn:
        row = _wunsch(conn, fig_no)
    if row is None:
        if not [k for k in body.teile_da if k.strip()]:
            return {"ok": True, "da": 0, "gesamt": 0, "neu": False}
        main.add_wanted(main.WantedBody(
            item_id=fig_no, item_type="minifig", name=body.name or fig_no,
            img_url=body.img_url if re.match(main.IMG_URL_RE, body.img_url) else "",
            bricklink_url=body.bricklink_url, year=body.year), user)
        with core.db() as conn:
            row = _wunsch(conn, fig_no)
        if row is None:
            # `add_wanted` stellt die Nummer auf BrickLink um; kommt sie hier
            # schon anders an, gibt es nichts, woran ein Haken hängen kann.
            raise HTTPException(409, "Figur nicht auf der Wunschliste")
        neu = True
    r = haken_setzen(row["id"], HakenBody(teile_da=body.teile_da), user)
    return {**r, "neu": neu}


# ── In welchen Figuren steckt ein Teil? ───────────────────────────────
#
# Von der Einzelteilsuche zum Puzzle: Wer einen weißen Stormtrooper-Helm
# gefunden hat, will wissen, welche Figuren ihn tragen – und dort die Teile
# abhaken, die er schon hat (3.9.14, 11.10.2026).

_figuren_merker: dict = {"stand": None, "index": {}}


def _figuren_je_teil() -> dict:
    """(Teil, Farbe) → Figuren. Einmal je Datei, wie `_alle_teile`."""
    stand = core.get_setting("figurteile_geholt_at") or ""
    if _figuren_merker["stand"] == stand and _figuren_merker["index"]:
        return _figuren_merker["index"]
    index: dict = {}
    with core.db() as conn:
        migrieren(conn)
        for item_no, roh in conn.execute("SELECT item_no, teile FROM figurteile"):
            try:
                for t in json.loads(roh):
                    liste = index.setdefault((str(t[0]), str(t[1])), [])
                    if item_no not in liste:
                        liste.append(item_no)
            except (ValueError, IndexError, TypeError):
                continue
    _figuren_merker.update({"stand": stand, "index": index})
    return index


def in_figuren(nr: str, farbe: str = "", hoechstens: int = 30) -> dict:
    """Ohne Farbe (Steckbrief aus einer Liste, die keine kennt) zählt das
    Teil in jeder Farbe."""
    index = _figuren_je_teil()
    if farbe != "":
        nummern = list(index.get((nr, str(farbe)), []))
    else:
        nummern = []
        for (n, _), liste in index.items():
            if n == nr:
                nummern += [x for x in liste if x not in nummern]
    nummern.sort()
    zeigen = nummern[:hoechstens]
    namen = {}
    if zeigen:
        marken = ",".join("?" * len(zeigen))
        with core.db() as conn:
            for tab, spalte in (("katalog_index", "item_no"),
                                ("collection", "item_id"), ("wanted", "item_id")):
                try:
                    for r in conn.execute(
                            f"SELECT {spalte}, name FROM {tab} WHERE lower({spalte})"
                            f" IN ({marken}) AND name != ''",
                            [z.lower() for z in zeigen]):
                        namen.setdefault(r[0].lower(), r[1])
                except Exception:
                    continue
    return {"figuren": [{"item_no": n, "name": namen.get(n.lower(), ""),
                         "img_url": integrations.minifig_bild(n)} for n in zeigen],
            "weitere": max(0, len(nummern) - len(zeigen))}


@router.get("/api/figurteile/teil/{nr}")
def teil_in_figuren(nr: str, farbe: str = "", user: dict = Depends(_benutzer)):
    return in_figuren(nr, farbe)


@router.post("/api/figurteile/holen")
def holen(user: dict = Depends(_admin)):
    try:
        return ziehen()
    except Exception as e:
        raise HTTPException(502, str(e)[:200])


# ── Einzelteile suchen ────────────────────────────────────────────────
#
# Anlass (10.10.2026): „weißer stormtrooper helm mit gelb aufdruck" fand im
# manuellen Erfassen mit Typ „Einzelteil" nichts. Teile fragte die Instanz
# nur bei Rebrickable, und dort sind die Namen englisch und **farblos** –
# ein Teil gibt es dort in jeder Farbe unter derselben Nummer. Aus „weißer"
# wird zwar `white`, aber `white` steht in keinem Teilenamen, und die Suche
# verlangt alle Wörter.
#
# Die Teile der Figuren liegen seit 3.9.0 ohnehin hier, je Figur mit
# BrickLink-Nummer **und Farbe**, die Namen sind BrickLinks beschreibende
# („Minifigure, Headgear Helmet SW Stormtrooper Pattern"). Genau diese
# Teile sucht man fast immer: Helme, Köpfe, Torsos, Beine. Keine
# Bildbeschreibung nötig – der Name beschreibt schon.

# Wörter, die einen Aufdruck meinen. BrickLink schreibt „Pattern", neuere
# Namen „Print"; die Nummer trägt ihn als `pb`/`pr` („30408pb01").
DRUCK = frozenset({"print", "printing", "printed", "pattern", "aufdruck",
                   "bedruckt", "druck", "muster"})
# Englische Farbwörter, wie sie in BrickLinks Farbnamen stehen.
FARBEN_EN = frozenset("""red blue green yellow black white tan orange brown
gray grey silver gold pink purple azure lime olive magenta lavender turquoise
bronze copper beige maroon teal nougat trans chrome pearl metallic""".split())

_GEDRUCKT = re.compile(r"p[br]\d")
_teile_merker: dict = {"stand": None, "teile": []}


def _worte(text: str) -> list:
    return [w for w in re.split(r"[^a-z0-9]+", core.falten(text)) if w]


def _alle_teile() -> list:
    """Jedes Teil in jeder Farbe, die an einer Figur vorkommt – einmal je
    Datei aufgebaut. Rund 40.000 Paare; im Speicher schneller als jede
    Abfrage, und es ändert sich nur, wenn eine neue Datei kommt."""
    stand = core.get_setting("figurteile_geholt_at") or ""
    if _teile_merker["stand"] == stand and _teile_merker["teile"]:
        return _teile_merker["teile"]
    haeufig: dict = {}
    with core.db() as conn:
        migrieren(conn)
        for (roh,) in conn.execute("SELECT teile FROM figurteile"):
            try:
                for t in json.loads(roh):
                    k = (str(t[0]), t[1])
                    haeufig[k] = haeufig.get(k, 0) + 1
            except (ValueError, IndexError, TypeError):
                continue
        info = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT nr, name, art FROM figurteile_teil")}
    farben = _farben()
    teile = []
    for (nr, farbe), n in haeufig.items():
        name = info.get(nr, ("", ""))[0]
        if not name:
            continue                    # ohne Namen nicht zu finden
        fname = farben.get(str(farbe), "")
        teile.append((nr, farbe, n, _worte(name), _worte(fname),
                      core.falten(nr)))
    teile.sort(key=lambda t: -t[2])      # oft verbaute Teile zuerst
    _teile_merker.update({"stand": stand, "teile": teile})
    return teile


def _bedingungen(q: str) -> list:
    """Je Wort der Anfrage: (Art, Varianten). Art ist `farbe`, `druck`
    oder `wort`; jede Variante ist eine Wortliste, die ganz passen muss
    („bluish gray", „battle droid")."""
    import woerterbuch
    aus = []
    for w in woerterbuch.anfrage_teilen(q):
        varianten = [[core.falten(w)]]
        for e in list(woerterbuch.nachschlagen(w)) + list(woerterbuch._fassungen(w)):
            v = _worte(e)
            if v and v not in varianten:
                varianten.append(v)
        flach = {x for v in varianten for x in v}
        if flach & DRUCK:
            aus.append(("druck", varianten))
        elif any(len(v) == 1 and v[0] in FARBEN_EN for v in varianten):
            aus.append(("farbe", [v for v in varianten if v[0] in FARBEN_EN]))
        else:
            aus.append(("wort", varianten))
    return aus


def _steht(variante: list, worte: list) -> bool:
    """Jedes Wort der Variante beginnt ein Wort im Namen – „helmet" trifft
    „Helmet", „trooper" auch „Troopers", aber „king" kein „Markings"."""
    return all(any(n.startswith(v) for n in worte) for v in variante)


def suchen(q: str, hoechstens: int = 40) -> list:
    """Einzelteile nach Name und Farbe.

    **Farbe zählt doppelt.** „weißer Helm mit gelbem Aufdruck" nennt zwei
    Farben, und welche die des Teils ist, sagt erst der Satzbau. Deshalb
    darf jede Farbe in der Teilefarbe **oder** im Namen stehen („with
    Yellow …"); vorn steht, was in der Teilefarbe die erste genannte
    Farbe trägt.
    """
    bedingungen = _bedingungen(q)
    if not bedingungen or all(a != "wort" for a, _ in bedingungen):
        return []                        # nur „weiß" ist keine Teilesuche
    erste_farbe = next((v for a, v in bedingungen if a == "farbe"), None)
    treffer = []
    for nr, farbe, n, name, fname, nr_f in _alle_teile():
        ok = True
        for art, varianten in bedingungen:
            if art == "druck":
                ok = ("pattern" in name or "print" in name
                      or _GEDRUCKT.search(nr_f) is not None)
            elif art == "farbe":
                ok = any(_steht(v, fname) or _steht(v, name) for v in varianten)
            else:
                ok = (any(_steht(v, name) for v in varianten)
                      or any(nr_f.startswith(v[0]) for v in varianten if len(v) == 1))
            if not ok:
                break
        if not ok:
            continue
        vorn = bool(erste_farbe) and any(_steht(v, fname) for v in erste_farbe)
        treffer.append((0 if vorn else 1, -n, nr, farbe))
    treffer.sort()
    with core.db() as conn:
        info = _info(conn, {t[2] for t in treffer[:hoechstens]})
    farben = _farben()
    aus = []
    for _, _, nr, farbe in treffer[:hoechstens]:
        t = _teil(nr, farbe, 1, info, farben)
        # Die Farbe gehört in den Namen: Die Sammlung kennt bei Teilen
        # keine eigene Farbspalte, und ein weißer und ein schwarzer Helm
        # sähen dort sonst gleich aus.
        if t["color_name"]:
            t["name"] = "%s (%s)" % (t["name"], t["color_name"])
        t["sub"] = t["color_name"]
        t["year"] = 0
        aus.append(t)
    return aus
