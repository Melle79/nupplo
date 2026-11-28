"""Preise aus dem Nupplo-Hub statt von BrickLink (Labor, 3.9.0).

**Warum.** Bisher fragt jede Instanz ihre Preise mit eigenem BrickLink-
Schlüssel ab. Seit Hub 1.28.0 holt der Katalogdienst die Preise einmal für
alle und legt sie im Hub ab. Diese Instanz kann sie von dort übernehmen –
dann braucht sie für Preise keinen eigenen Schlüssel mehr.

**Wie, ohne die Sammlung zu verraten.** Geholt wird der **ganze Tagesabzug**
eines Gebiets (`GET /v1/preise/abzug`, gzip-NDJSON), nicht die einzelnen
Nummern. Nachgeschlagen wird danach nur noch lokal in `hub_preise`. Der Hub
erfährt also nie, welche Figuren hier liegen. Der Abzug geht einmal am Tag
je Zugang und Gebiet; ein unveränderter Stand (ETag) kostet nichts.

**Zugang.** Ein eigener, anonymer Preis-Zugang (`POST /v1/preise/zugang`),
nicht der Token des Tausch-Netzes: Preise soll auch bekommen, wer nie
beigetreten ist, und der Abzug soll nicht an einem Mitglied hängen.

**Entweder – oder** (3.9.17, Sven 14.10.2026: „Entweder oder würde ich
sagen.“). Mit eigenem BrickLink-Schlüssel holt die Instanz alle Preise
selbst und nimmt nie die zentralen; ihre Preise gehen nie zum Hub. Ohne
Schlüssel gibt es nur die Preise aus dem Hub – und die nur für Star Wars und
Herr der Ringe. Einen Schalter dafür gibt es nicht mehr.

**Figurenpreise nie älter als `FRISCH_STUNDEN`.** Ältere Figurenpreise
werden nur noch mit ihrem Datum vermerkt. Alte Setpreise bleiben dagegen
mit ihrem Datum verfügbar, bis der Katalogdienst einen neueren Preis
liefert; Sets werden im Restbudget nach den Figuren aktualisiert.
Mit eigenem Schlüssel wählt der Betreiber selbst, wie oft geholt wird
(`main.preisabruf_stunden`).
"""
import gzip
import json
import os
import sqlite3
import time

import requests

import core
import hub

TIMEOUT = 60
# So alt darf der ganze Abzug werden, bevor wieder ein ganzer geholt wird.
# Der Hub lässt ohnehin nur einen am Tag je Gebiet durch.
ABZUG_ALTER = 20 * 3600
# Dazwischen stündlich nur das Neue (`seit`, Hub-PR #12).
NACHLADEN_ALLE = 3600
TYPEN = {"minifig": "minifig", "set": "set", "part": "part"}

# **Die eine Stelle für die Figurengrenze.** BrickLink nennt 24 Stunden;
# Sven hat am 15.10.2026 26 gewählt. Ändert sich das, ändert sich nur diese Zahl.
FRISCH_STUNDEN = 26
FRISCH_S = FRISCH_STUNDEN * 3600
# Ab wann ein Preis mit „Stand <Datum>“ erscheint (wie Hub 1.44.0
# `datum_ab_stunden`).
STAND_AB_STUNDEN = 48


class KeinHubPreis(RuntimeError):
    """Der Hub hat zu diesem Artikel (noch) keinen Preis.

    `zuletzt` (3.9.27): wann der Preisdienst ihn zuletzt geholt hat, falls
    bekannt. Für Figuren wird nur das Datum, nie der alte Preis verwendet.
    """

    def __init__(self, text: str, zuletzt: int | None = None):
        super().__init__(text)
        self.zuletzt = zuletzt


def an() -> bool:
    """Preise aus dem Hub – genau dann, wenn kein eigener Schlüssel da ist."""
    import integrations
    try:
        return not integrations.bricklink_enabled()
    except sqlite3.Error:       # noch keine Datenbank (Start, Tests)
        return False


def hub_url() -> str:
    """Woher die Preise kommen. Meist derselbe Hub wie das Tausch-Netz;
    `PREIS_HUB_URL` trennt beides – etwa für eine Instanz, die im echten
    Tausch-Netz bleibt, ihre Preise aber schon vom Test-Hub holt (eine Testinstanz
    Instanz im Oktober 2026, solange der Live-Hub den neuen Preisweg noch
    nicht kann)."""
    return (os.environ.get("PREIS_HUB_URL", "").strip().rstrip("/")
            or hub.HUB_URL)


def katalog_datei(name: str, kopf: dict, timeout: int = 120):
    """Teile- oder Setdatei beim Hub holen (Hub 1.41.0).

    Sven wollte die beiden Dateien nicht auf GitHub haben (17.10.2026); sie
    liegen deshalb wie die Preise im Hub. Die Anmeldung ist derselbe
    anonyme Preis-Zugang – er sagt dem Hub nichts über die Sammlung. Ein
    abgelehnter Zugang (401, etwa nach einem Hub-Neuaufbau) wird einmal
    neu angelegt. Der Hub schickt die Datei mit `content-encoding: gzip`,
    `requests` entpackt von selbst; `kopf` trägt If-None-Match.
    """
    url = hub_url() + "/v1/katalog/" + name
    for versuch in (1, 2):
        r = requests.get(url, headers={**kopf, "Authorization": "Bearer " + _token()},
                         timeout=timeout)
        if r.status_code != 401 or versuch == 2:
            return r
        core.set_setting("hub_preis_token", "")
    return r


def frisch_ab() -> int:
    """Frischegrenze für Figurenpreise und Preisverläufe."""
    return int(time.time()) - FRISCH_S


def _tabelle(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hub_preise (
            t TEXT NOT NULL, nr TEXT NOT NULL, z TEXT NOT NULL,
            art TEXT NOT NULL, gebiet TEXT NOT NULL,
            genutzt TEXT, min REAL, avg REAL, max REAL, anzahl INTEGER,
            geholt_am INTEGER,
            PRIMARY KEY (t, nr, z, art, gebiet)
        )""")


def _tabelle_alt(conn):
    """Nur Datum, kein Preis: wann der Preisdienst einen Satz zuletzt geholt
    hat, der hier nicht mehr frisch ist (3.9.27, Sven 22.10.2026: „Hinweis
    mit Datum“ statt „Kein aktueller Preis“)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hub_preise_alt (
            t TEXT NOT NULL, nr TEXT NOT NULL, z TEXT NOT NULL,
            art TEXT NOT NULL, gebiet TEXT NOT NULL, geholt_am INTEGER,
            PRIMARY KEY (t, nr, z, art, gebiet)
        )""")


def _stand() -> dict:
    try:
        d = json.loads(core.get_setting("hub_preise_stand") or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


# ------------------------------------------- Nur für Nupplo (3.9.32)
#
# Stufe 1 des Schutzes vor fremden Forks (30.10.2026): Jeder Aufruf
# unter /v1/preise/ trägt Programm, Fassung, Zeit und ein HMAC-Zeichen. Der
# Schlüssel steckt im Programm, nicht in der Instanz – ein Fork, der ihn
# herausliest, ist damit nicht ausgesperrt, aber erkennbar, und die
# Hub-Verwaltung kann ihn abschalten. Ab 3.9.32.
PROGRAMM = "se"
_SCHLUESSEL = "t5eelREOKBg1YIgbPArJt6dEyEoO2t5Dd5ufcAJlnOk="
ABWEICHUNG_S = 600          # so weit darf die Uhr abweichen


def _fassung() -> str:
    import re
    f = str(getattr(core, "APP_VERSION", "0") or "0").lower()
    f = re.sub(r"[^0-9a-z.+-]", "", f)[:20]
    return f if f[:1].isdigit() else "0" + f[:19]


def _uhr() -> int:
    """Eigene Uhr plus der Versatz, den der Hub zuletzt gemeldet hat."""
    try:
        versatz = int(core.get_setting("hub_uhr_versatz") or 0)
    except (ValueError, sqlite3.Error):
        versatz = 0
    return int(time.time()) + versatz


def zeichen(methode: str, pfad: str, zeit: int | None = None,
            programm: str = PROGRAMM, fassung: str | None = None,
            schluessel: str = _SCHLUESSEL) -> dict:
    """Die drei Köpfe für einen Aufruf. `pfad` ohne Query."""
    import base64
    import hashlib
    import hmac
    fassung = fassung or _fassung()
    zeit = _uhr() if zeit is None else int(zeit)
    kennung = "%s/%s" % (programm, fassung)
    text = "%s\n%d\n%s %s" % (kennung, zeit, methode.upper(), pfad)
    sig = hmac.new(base64.b64decode(schluessel), text.encode(),
                   hashlib.sha256).hexdigest()
    return {"x-nupplo-programm": kennung, "x-nupplo-zeit": str(zeit),
            "x-nupplo-zeichen": sig}


def _kopf(methode: str, pfad: str, mit_token: bool = True) -> dict:
    k = {"user-agent": hub.USER_AGENT}
    k.update(zeichen(methode, pfad))
    if mit_token:
        k["authorization"] = "Bearer " + _token()
    return k


def _uhr_nachstellen(resp) -> None:
    """Meldet der Hub seine Zeit und weicht sie mehr als 10 Minuten ab,
    den Versatz merken – sonst würde jeder weitere Aufruf abgewiesen."""
    try:
        zeit = int((resp.json() or {}).get("zeit") or 0)
    except Exception:
        return
    if zeit and abs(zeit - int(time.time())) > ABWEICHUNG_S:
        core.set_setting("hub_uhr_versatz", str(zeit - int(time.time())))


def abgeschaltet(resp) -> bool:
    """503 {"abgeschaltet": true} ist die Notbremse des Hubs, kein Fehler:
    Preise bleiben, wie sie sind, und frühestens nach Retry-After wird es
    wieder versucht."""
    if resp.status_code != 503:
        return False
    try:
        d = resp.json() or {}
    except Exception:
        return False
    if not d.get("abgeschaltet"):
        return False
    _uhr_nachstellen(resp)
    try:
        warten = int(resp.headers.get("retry-after") or 3600)
    except ValueError:
        warten = 3600
    core.set_setting("hub_preise_ruhe_bis", str(int(time.time()) + max(60, warten)))
    return True


def ruht() -> bool:
    try:
        return int(core.get_setting("hub_preise_ruhe_bis") or 0) > time.time()
    except ValueError:
        return False


def _token() -> str:
    tok = core.get_setting("hub_preis_token")
    if tok:
        return tok
    resp = requests.post(hub_url() + "/v1/preise/zugang",
                         headers=_kopf("POST", "/v1/preise/zugang", False),
                         timeout=TIMEOUT)
    if abgeschaltet(resp):
        raise RuntimeError("Preisdienst ruht gerade")
    if resp.status_code == 429:
        # Heute keine neuen Zugänge – morgen wieder.
        core.set_setting("hub_preise_ruhe_bis", str(int(time.time()) + 6 * 3600))
        raise RuntimeError("Preisdienst gibt heute keine neuen Zugänge aus")
    _uhr_nachstellen(resp) if resp.status_code == 401 else None
    resp.raise_for_status()
    tok = (resp.json() or {}).get("token") or ""
    if not tok:
        raise RuntimeError("Hub gab keinen Preis-Zugang heraus")
    core.set_setting("hub_preis_token", tok)
    return tok


def laden(gebiet: str, erzwingen: bool = False) -> dict:
    """Preise eines Gebiets holen und lokal ablegen.

    **Einmal am Tag ganz, dazwischen stündlich nur das Neue** (Hub-PR #12).
    Ein ganzer Abzug enthält frische Preise und ältere Sätze mit dem Feld
    `alt`. Die App behält alte Setbeträge, aber nur das Datum alter
    Figurenpreise. Jetzt fragt die Instanz jede Stunde mit
    `seit=<bis des letzten Abzugs>` und bekommt nur, was seither geholt wurde
    (`teil: true`). Das ersetzt die Zeilen gleichen Schlüssels.

    Zurück kommt, was passiert ist: `neu` (Anzahl Sätze, mit `teil`),
    `unveraendert` (304), `spaeter` (429) oder `uebersprungen`.
    """
    stand = _stand()
    eintrag = stand.get(gebiet) or {}
    jetzt = int(time.time())
    voll = (not eintrag.get("geholt_am")
            or jetzt - eintrag["geholt_am"] >= ABZUG_ALTER)
    if not voll and not erzwingen and \
            jetzt - (eintrag.get("nach_am") or 0) < NACHLADEN_ALLE:
        return {"uebersprungen": True}
    if ruht() and not erzwingen:
        return {"abgeschaltet": True}
    kopf = _kopf("GET", "/v1/preise/abzug")
    params = {"gebiet": gebiet}
    if voll:
        # Hub 1.43.0: hängt für Nummern ohne frischen Satz das letzte
        # geholt_am an (`alt: true`, ohne Preisfelder). Ältere Hubs
        # übergehen den Schalter. Das ETag unterscheidet die Antwort mit
        # `mit_alt` nicht; nach dem Wechsel muss der erste Abzug deshalb
        # vollständig kommen, sonst blieben alte Setdaten unsichtbar.
        params["mit_alt"] = "1"
        if eintrag.get("mit_alt") and eintrag.get("etag"):
            kopf["if-none-match"] = eintrag["etag"]
    else:
        # Vor PR #12 gab es kein `bis`; dann zählt, wann wir geholt haben –
        # sonst hinge die Instanz nach dem Update bis morgen am Tagesabzug.
        params["seit"] = str(eintrag.get("bis") or eintrag["geholt_am"])
    resp = requests.get(hub_url() + "/v1/preise/abzug",
                        params=params, headers=kopf, timeout=TIMEOUT)
    if abgeschaltet(resp):
        return {"abgeschaltet": True}
    if resp.status_code == 401:
        _uhr_nachstellen(resp)
        # Zugang weg (z. B. Hub zurückgesetzt) – beim nächsten Mal neu holen.
        core.set_setting("hub_preis_token", "")
        resp.raise_for_status()
    if resp.status_code == 304:
        eintrag["nach_am"] = jetzt
        if voll:
            eintrag.update(geholt_am=jetzt, mit_alt=True)
        stand[gebiet] = eintrag
        core.set_setting("hub_preise_stand", json.dumps(stand))
        _alte_weg(gebiet)
        return {"unveraendert": True}
    if resp.status_code == 429:
        return {"spaeter": True}
    resp.raise_for_status()
    roh = resp.content
    # requests packt `content-encoding: gzip` selbst aus, eine angehängte
    # .gz-Datei (`content-type: application/gzip`) aber nicht.
    if roh[:2] == b"\x1f\x8b":
        roh = gzip.decompress(roh)
    zeilen = roh.decode("utf-8").splitlines()
    kopfzeile = json.loads(zeilen[0]) if zeilen else {}
    teil = bool(kopfzeile.get("teil"))
    saetze, alte = [], []
    for z in zeilen[1:]:
        if not z.strip():
            continue
        try:
            s = json.loads(z)
        except ValueError:
            continue
        if s.get("t") not in TYPEN or s.get("z") not in ("N", "U") \
                or s.get("art") not in ("sold", "stock"):
            continue
        if s.get("alt"):
            alte.append((s["t"], str(s.get("nr") or ""), s["z"], s["art"],
                         gebiet, s.get("geholt_am")))
            # Seit 3.9.31 behalten auch Figuren ihren letzten Preis (Sven,
            # 30.10.2026: „Preise verschwinden nie“). Ohne Betrag bleibt nur
            # das Datum.
            if all(s.get(f) is None for f in ("min", "avg", "max", "anzahl")):
                continue
        saetze.append((s["t"], str(s.get("nr") or ""), s["z"], s["art"],
                       gebiet, s.get("genutzt"), s.get("min"), s.get("avg"),
                       s.get("max"), s.get("anzahl"), s.get("geholt_am")))
    with core.db() as conn:
        _tabelle(conn)
        if not teil:
            conn.execute("DELETE FROM hub_preise WHERE gebiet = ?", (gebiet,))
        conn.executemany(
            "INSERT OR REPLACE INTO hub_preise (t, nr, z, art, gebiet, genutzt,"
            " min, avg, max, anzahl, geholt_am) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            saetze)
        if alte:
            _tabelle_alt(conn)
            conn.executemany(
                "INSERT INTO hub_preise_alt (t, nr, z, art, gebiet, geholt_am)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT (t, nr, z, art, gebiet) DO"
                " UPDATE SET geholt_am = MAX(COALESCE(geholt_am, 0),"
                " COALESCE(excluded.geholt_am, 0))", alte)
    _alte_weg(gebiet)
    if kopfzeile.get("kurse"):
        core.set_setting("hub_preise_kurse", json.dumps(
            {"kurse": kopfzeile.get("kurse") or {},
             "stand": kopfzeile.get("stand")}))
    eintrag.update(bis=int(kopfzeile.get("bis") or jetzt), nach_am=jetzt)
    if teil:
        eintrag["anzahl_neu"] = len(saetze)
    else:
        eintrag.update(etag=resp.headers.get("etag") or "", geholt_am=jetzt,
                       anzahl=len(saetze), mit_alt=True)
    stand[gebiet] = eintrag
    core.set_setting("hub_preise_stand", json.dumps(stand))
    return {"neu": len(saetze), "teil": teil}


def _alte_weg(gebiet: str) -> None:
    """Nichts mehr löschen (3.9.31).

    Bis 3.9.30 verloren Figurenpreise nach 26 Stunden ihren Betrag. Holte
    der Preisdienst eine Figur nicht rechtzeitig nach, stand sie in der
    Sammlung ohne Preis da – in einer Testsammlung am 30.10.2026 bei 173 statt
    161 Einträgen. Jetzt bleibt der letzte Preis mit seinem `geholt_am`
    stehen, bis der Hub einen neueren liefert; die Anzeige nennt ab
    `STAND_AB_STUNDEN` das Datum. Die Funktion bleibt als Haken stehen,
    damit Aufrufer unverändert bleiben."""
    return None


def laden_kette(gebiet: str, erzwingen: bool = False) -> dict:
    """Den Abzug des eigenen Gebiets holen – und die Rückfälle der Kette
    gleich mit, damit `nachschlagen` dort suchen kann. Zurück kommt das
    Ergebnis fürs eigene Gebiet; Fehler der Rückfälle stören es nicht."""
    erg = laden(gebiet, erzwingen)
    for g in kette(gebiet)[1:]:
        try:
            laden(g, erzwingen)
        except Exception as e:
            print(f"[nupplo] Hub-Preise ({g or 'weltweit'}) übersprungen: {e}",
                  flush=True)
    return erg


def _kurs(waehrung: str) -> float | None:
    if waehrung == "EUR":
        return 1.0
    try:
        k = json.loads(core.get_setting("hub_preise_kurse") or "{}")
        wert = float((k.get("kurse") or {}).get(waehrung) or 0)
    except (ValueError, TypeError, AttributeError):
        return None
    return wert if wert > 0 else None


# Letzter Rückfall: weltweit. Seit Katalogdienst 1.24.0 holt der Dienst
# Verkäufe einmal weltweit und rechnet Länder und Kontinente daraus – einen
# weltweiten Satz gibt es damit für jede geholte Figur, für Länder und
# Kontinente nur, wo dort verkauft wurde.
RUECKFAELLE = ("",)


def kette(gebiet: str) -> list:
    """Wo gesucht wird: das eigene Gebiet, sein Kontinent, zuletzt weltweit
    (`RUECKFAELLE`).

    Der Katalogdienst holt nur Deutschland ganz; andere Gebiete bekommen
    nur, wonach dort im Tausch gefragt wird (Katalogdienst 1.23.0). Eine
    Installation auf einem Kontinent, für den es noch nichts gibt, soll
    trotzdem einen Preis sehen – und erkennen, woher er stammt (`used_scope`).
    """
    import integrations
    aus = []
    for g in (gebiet, integrations.LAND_REGION.get(gebiet), *RUECKFAELLE):
        if g is not None and g not in aus:
            aus.append(g)
    return aus


def _datum(ts: int) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y")


def zuletzt_geholt(t: str, item_no: str, condition: str, guide_type: str,
                   gebiet: str) -> int | None:
    """Wann es zuletzt einen Satz gab – entlang der Kette, das jüngste.

    Nur fürs Datum im Hinweis. Ein Satz, der hier noch steht, aber nicht
    mehr frisch ist, zählt mit (bis `_alte_weg` ihn wegräumt)."""
    g = kette(gebiet)
    platz = ",".join("?" * len(g))
    with core.db() as conn:
        _tabelle(conn)
        _tabelle_alt(conn)
        werte = [conn.execute(
            "SELECT MAX(geholt_am) FROM %s WHERE t = ? AND lower(nr) = ? AND"
            " z = ? AND art = ? AND gebiet IN (%s)" % (tab, platz),
            (t, item_no.lower(), condition, guide_type, *g)).fetchone()[0]
            for tab in ("hub_preise", "hub_preise_alt")]
    werte = [w for w in werte if w]
    return max(werte) if werte else None


def nachschlagen(item_type: str, item_no: str, condition: str, gebiet: str,
                 waehrung: str, guide_type: str) -> dict:
    """Ein Preis aus dem lokalen Abzug – im Format von `price_guide`.

    Gesucht wird entlang `kette(gebiet)`; ein Satz ohne Verkauf zählt nicht,
    solange ein späteres Gebiet einen hat."""
    t = TYPEN.get(item_type.lower())
    if not t:
        raise ValueError(f"Unbekannter Typ: {item_type}")
    if t == "set" and "-" not in item_no:
        item_no = f"{item_no}-1"
    # Seit Hub 1.35.0 stehen alle Nummern klein im Abzug; wer „Sw0312“
    # eingetippt hat, fand den Preis nicht. Ältere Abzüge können noch
    # gemischt sein – deshalb auf beiden Seiten klein vergleichen.
    r, quelle = None, gebiet
    with core.db() as conn:
        _tabelle(conn)
        for g in kette(gebiet):
            # Seit 3.9.31 auch ältere Sätze (Figuren wie Sets): Ein Preis
            # verschwindet nicht, nur weil er nicht frisch ist – die Anzeige
            # sagt ab `STAND_AB_STUNDEN`, von wann er ist.
            zeile = conn.execute(
                "SELECT genutzt, min, avg, max, anzahl, geholt_am FROM "
                "hub_preise WHERE t = ? AND lower(nr) = ? AND z = ? AND "
                "art = ? AND gebiet = ?",
                (t, item_no.lower(), condition, guide_type, g)).fetchone()
            if not zeile:
                continue
            if r is None:
                r, quelle = zeile, g
            hat = zeile["avg"] is not None if guide_type == "sold" \
                else (zeile["anzahl"] or 0) > 0
            if hat:
                r, quelle = zeile, g
                break
    if not r:
        zuletzt = zuletzt_geholt(t, item_no, condition, guide_type, gebiet)
        if zuletzt:
            raise KeinHubPreis(
                "Preis zuletzt am %s geholt, wird bald aktualisiert."
                % _datum(zuletzt), zuletzt)
        raise KeinHubPreis("Kein aktueller Preis im Preisdienst.")
    kurs = _kurs(waehrung)
    if kurs is None:            # Kurs fehlt: lieber Euro als falsch umgerechnet
        waehrung, kurs = "EUR", 1.0

    def um(x):
        # Auf Cent gerundet (3.9.32): Die Nachkommastellen dahinter sind das
        # Wasserzeichen des Preisdiensts und gehen nicht weiter.
        return None if x is None else f"{round(x * kurs, 2):.4f}"

    gefunden = r["avg"] is not None if guide_type == "sold" \
        else (r["anzahl"] or 0) > 0
    # Aus einem anderen Gebiet der Kette: dessen Rückfall zählt mit – ein
    # leerer Wert hieße beim Hub „weltweit“.
    genutzt = r["genutzt"] if r["genutzt"] is not None else quelle
    if quelle != gebiet and not genutzt:
        genutzt = quelle
    out = {
        "currency": waehrung,
        "min": um(r["min"]) if gefunden else None,
        "avg": um(r["avg"]) if gefunden else None,
        "max": um(r["max"]) if gefunden else None,
        "times_sold": r["anzahl"],
        "condition": condition,
        "scope": gebiet,
        "used_scope": genutzt,
        "fell_back": genutzt != gebiet,
        "guide_type": guide_type,
        "quelle": "hub",
        # Wann der Katalogdienst ihn bei BrickLink geholt hat – danach
        # richtet sich, wie lange er gezeigt werden darf.
        "geholt_am": r["geholt_am"],
        # Älter als `STAND_AB_STUNDEN`: die Anzeige nennt das Datum.
        "stand": (_datum(r["geholt_am"]) if r["geholt_am"] and
                  r["geholt_am"] < int(time.time()) - STAND_AB_STUNDEN * 3600
                  else None),
    }
    if guide_type == "stock":
        out["angebote"] = r["anzahl"] if gefunden else 0
        out["times_sold"] = None
    return out


def anfrage(item_no: str, gebiet: str) -> str:
    """Ein Set auf Zuruf: `frisch`, `vorgemerkt` oder `nicht_im_dienst`.

    Nur für Sets und nur, wenn jemand es öffnet – der Hub erfährt so eine
    einzelne Nummer, aber nicht, wer fragt oder was sonst hier liegt. Ein
    frischer Preis landet gleich in `hub_preise`, damit `nachschlagen` ihn
    findet.
    """
    if "-" not in item_no:
        item_no = f"{item_no}-1"
    resp = requests.post(hub_url() + "/v1/preise/anfrage",
                         json={"t": "set", "nr": item_no.lower(),
                               "gebiet": gebiet},
                         headers=_kopf("POST", "/v1/preise/anfrage"),
                         timeout=20)
    if abgeschaltet(resp):
        return "vorgemerkt"
    if resp.status_code == 401:
        _uhr_nachstellen(resp)
        core.set_setting("hub_preis_token", "")
    resp.raise_for_status()
    d = resp.json() or {}
    status = d.get("status") or ""
    if status == "frisch":
        saetze = [(s.get("t") or "set", str(s.get("nr") or item_no), s.get("z"),
                   s.get("art") or "sold", gebiet, s.get("genutzt"),
                   s.get("min"), s.get("avg"), s.get("max"), s.get("anzahl"),
                   s.get("geholt_am"))
                  for s in d.get("preise") or []
                  if s.get("z") in ("N", "U")]
        with core.db() as conn:
            _tabelle(conn)
            conn.executemany(
                "INSERT OR REPLACE INTO hub_preise (t, nr, z, art, gebiet, "
                "genutzt, min, avg, max, anzahl, geholt_am) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", saetze)
    return status


def status() -> dict:
    with core.db() as conn:
        _tabelle(conn)
        n = conn.execute("SELECT COUNT(*) FROM hub_preise").fetchone()[0]
    try:
        kurse = json.loads(core.get_setting("hub_preise_kurse") or "{}")
    except ValueError:
        kurse = {}
    return {"an": an(), "saetze": n, "gebiete": _stand(),
            "kurse_stand": kurse.get("stand")}


def waechter(gebiet_fn):
    """Läuft im Hintergrund: stündlich `laden` – einmal am Tag ganz, sonst
    nur das Neue seit dem letzten Abzug."""
    time.sleep(90)
    while True:
        if an():
            # Je Gebiet einzeln, wie `laden_kette`: Ein Fehler beim eigenen
            # Gebiet ließ sonst auch Kontinent und weltweit aus – genau die
            # Rückfälle, die dann gebraucht würden (Durchsicht 17.10.2026).
            try:
                gebiete = kette(gebiet_fn())
            except Exception as e:
                print(f"[nupplo] Hub-Preise übersprungen: {e}", flush=True)
                gebiete = []
            for g in gebiete:
                try:
                    erg = laden(g)
                except Exception as e:
                    print(f"[nupplo] Hub-Preise ({g or 'weltweit'}) übersprungen: {e}",
                          flush=True)
                    continue
                if erg.get("neu") is not None:
                    print(f"[nupplo] Hub-Preise ({g or 'weltweit'}) geladen: "
                          f"{erg['neu']} Sätze", flush=True)
        time.sleep(3600)
