"""Brickmerges Angebotspreis für den Set-Steckbrief (3.9.4, seit 3.9.12 nur der).

**Wozu.** „ich würde den Preis vom gerät abrufen lassen, wenn man auf den
Steckbrief geht und dann den link auf die Brickmegre-seite machen“ (10.10.2026).

**Nur noch der Preis.** Bis 3.9.11 holte dieses Modul auch Erscheinung,
EOL, UVP und Bilder von der Setseite, jede Instanz für sich. Seit 3.9.12
fragt der Katalogdienst Brickmerge im Takt und liefert all das in der
Setdatei („wobei ich gedacht hätte, die setdaten ziehen wir uns selber und
diese werden im katalog hinterlegt“, Sven). Hier bleibt der Angebotspreis,
weil er sich stündlich ändert – und nur, wenn jemand ihn unter „Mehr →
Angebotspreise“ eingeschaltet hat und den Steckbrief öffnet. Sonst schickt
der Steckbrief nichts nach draußen. Je Set und Tag einmal; was schiefgeht,
zeigt nur den Link.

**Gelesen wird der Text der Seite,** nicht ihr Aufbau: „akt. brickmerge
Preis: ab 715,24 €“ stand am 10.10.2026 so auf jeder Setseite; eine neue
Gestaltung lässt den Satz wahrscheinlicher stehen als ein bestimmtes
`<div>`. `lesen()` nimmt weiter alles mit, was dasteht – die Tests halten
fest, dass die Seite noch so aussieht, wie der Katalogdienst sie liest.
"""
import html
import json
import re
import time
from datetime import date

import requests

import core
import integrations

BASIS = "https://www.brickmerge.de/"
# Die Setdatei nennt nur Dateinamen darin (siehe main._setbilder).
BILDORDNER = BASIS + "img/sets/l/"
FEHLER_PAUSE = 3600               # nach einem Fehler: eine Stunde nur der Link
# Hängt am Tag im Zwischenspeicher. Liest `lesen()` mehr als vorher (3.9.6:
# die Bilder), gelten die Einträge von heute als alt – sonst kämen die
# Bilder erst morgen, und genau heute wird es ausprobiert.
FASSUNG = 2

_NUMMER = re.compile(r"[0-9A-Za-z]{2,12}(-\d{1,2})?")
_BETRAG = r"(\d{1,3}(?:\.\d{3})*,\d\d)\s*€"


def nummer(nr: str) -> str:
    """„7190“ → „7190-1“; Unbrauchbares (eigene Sets, Pfade) → leer."""
    nr = str(nr or "").strip()
    if not _NUMMER.fullmatch(nr) or nr.startswith(("custom", "manuell")):
        return ""
    return nr if "-" in nr else nr + "-1"


def link(nr: str) -> str:
    """Brickmerge leitet `/<nr>` selbst auf die Seite mit Namen um."""
    return BASIS + nummer(nr)


def _betrag(wert: str):
    try:
        v = float(wert.replace(".", "").replace(",", "."))
    except ValueError:
        return None
    return v if 0 < v < 100000 else None


def _text(seite: str) -> str:
    seite = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", seite)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", seite)))


def lesen(seite: str) -> dict:
    """Die Angaben aus einer Setseite; was nicht dasteht, fehlt im Ergebnis.

    `erschienen`/`eol` wie in der Setdatei: „JJJJ-MM“ oder nur „JJJJ“,
    `eol_geschaetzt`, wenn Brickmerge „≈“ davorschreibt.
    """
    t = _text(seite)
    d: dict = {}

    def monat(m_monat, m_jahr):
        jahr = int(m_jahr)
        if not 1949 <= jahr <= date.today().year + 6:
            return ""
        if m_monat and 1 <= int(m_monat) <= 12:
            return "%04d-%02d" % (jahr, int(m_monat))
        return "%04d" % jahr

    m = re.search(r"Release:\s*(?:(\d{1,2})/)?((?:19|20)\d\d)\b", t)
    if m and monat(*m.groups()):
        d["erschienen"] = monat(*m.groups())
    m = re.search(r"EOL:\s*(≈|ca\.)?\s*(?:(\d{1,2})/)?((?:19|20)\d\d)\b", t)
    if m and monat(m.group(2), m.group(3)):
        d["eol"] = monat(m.group(2), m.group(3))
        if m.group(1):
            d["eol_geschaetzt"] = True
    # Die aktuelle UVP vor der ursprünglichen: LEGO hat 2023 viele Preise
    # angehoben, und Händler rechnen ihren Nachlass von der neuen.
    for muster in (r"akt\. UVP:\s*" + _BETRAG, r"UVP:\s*" + _BETRAG):
        m = re.search(muster, t)
        if m and _betrag(m.group(1)):
            d["uvp_eur"] = _betrag(m.group(1))
            break
    for muster in (r"brickmerge Preis:\s*ab\s*" + _BETRAG,
                   r"kostet aktuell ab\s*" + _BETRAG):
        m = re.search(muster, t)
        if m and _betrag(m.group(1)):
            d["ab_eur"] = _betrag(m.group(1))
            break
    bilder = _bilder(seite)
    if bilder:
        d["bilder"] = bilder
    return d


def _bilder(seite: str) -> list:
    """Die Setbilder aus dem JSON-LD der Seite (`Product.image`).

    Nur Brickmerges eigener Bildordner: Was dort sonst stünde, könnte
    irgendwohin zeigen, und der Server holt diese Bilder selbst (siehe
    `integrations.BILD_HOSTS`). Höchstens zwölf – mehr scrollt niemand.
    """
    out = []
    for block in re.findall(r'(?is)<script[^>]*ld\+json[^>]*>(.*?)</script>',
                            seite):
        try:
            daten = json.loads(block)
        except ValueError:
            continue
        for e in daten if isinstance(daten, list) else [daten]:
            if not isinstance(e, dict) or e.get("@type") != "Product":
                continue
            bild = e.get("image") or []
            for u in [bild] if isinstance(bild, str) else bild:
                if (isinstance(u, str) and u.startswith(BASIS + "img/sets/")
                        and u not in out):
                    out.append(u)
    return out[:12]


def _holen(nr: str) -> tuple:
    r = requests.get(link(nr), timeout=10, allow_redirects=True,
                     headers={"User-Agent": integrations.USER_AGENT})
    r.raise_for_status()
    # Unbekannte Nummern landen auf der Startseite oder der Suche; deren
    # Text hätte zufällig auch ein „ab … €“.
    if not re.search(r"/%s(_|$|\?)" % re.escape(nummer(nr)), r.url):
        return r.url, {}
    return r.url, lesen(r.text)


def abrufen(nr: str) -> dict:
    """{"url", …Angaben} – aus dem Tageszwischenspeicher oder frisch."""
    nr = nummer(nr)
    if not nr:
        return {}
    heute = "%s/%d" % (date.today().isoformat(), FASSUNG)
    jetzt = int(time.time())
    with core.db() as conn:
        row = conn.execute("SELECT * FROM brickmerge_seiten WHERE nr = ?",
                           (nr,)).fetchone()
    if row and row["tag"] == heute and (row["ok"] or
                                        jetzt - row["geholt"] < FEHLER_PAUSE):
        return {"url": row["url"] or link(nr), **json.loads(row["daten"] or "{}")}
    try:
        url, daten = _holen(nr)
        ok = 1
    except Exception as e:            # noqa: BLE001 – still, nur der Link
        print("[nupplo] Brickmerge %s: %s" % (nr, str(e)[:120]), flush=True)
        url, daten, ok = link(nr), {}, 0
    with core.db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO brickmerge_seiten (nr, tag, url, daten, ok,"
            " geholt) VALUES (?, ?, ?, ?, ?, ?)",
            (nr, heute, url, json.dumps(daten), ok, jetzt))
    return {"url": url, **daten}
