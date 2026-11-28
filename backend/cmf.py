"""Sammelfiguren-Schachteln am Code erkennen (Labor, 3.9.16).

**Warum.** Die Schachteln der Minifiguren-Serien (CMF) tragen einen
Data-Matrix-Code, und der verrät, welche Figur darin steckt – ohne die
Schachtel zu öffnen. Die Instanz liest ihn selbst, **bevor** Brickognize
gefragt wird: Das spart Kontingent, und das Foto bleibt zu Hause.

**Woher die Zuordnung kommt.** Der Katalogdienst veröffentlicht
`cmf-codes.ndjson` (`{"code": "6472867", "item_no": "col427"}` je Zeile).
Was die Liste nicht kennt, lernt die Instanz: Wer eine unbekannte Schachtel
öffnet und die Figur antippt, legt `code → col…` in `cmf_gelernt` ab. Das
Gelernte gilt vor der Liste und läuft **nicht** im Abgleich mit.

**Teilen** ist ein eigener Schalter (`cmf_teilen`), voreingestellt aus. Ist
er an, geht nur `{code, item_no}` an den Hub – keine Namen, nichts aus der
Sammlung.

Der Schlüssel ist die erste Ziffernfolge mit 6–12 Stellen; der Code trägt
vier Teile, z. B. „6472867 245S3 13916839 000341“.
"""
import io
import json
import os
import re
import time

import requests

import core
import integrations

CMF_QUELLE = ("https://raw.githubusercontent.com/Melle79/nupplo/main/"
              "katalog/cmf-codes.ndjson")
MAX_BYTES = 2 * 1024 * 1024
# Einmal geholt, eine Stunde lang nicht wieder fragen – ein Scan soll nicht
# jedes Mal auf den Katalogdienst warten.
FRISCH_S = 3600
NUMMER = re.compile(r"^col[a-z0-9]*\d+[a-z0-9]*$", re.I)
SCHLUESSEL = re.compile(r"(?<!\d)(\d{6,12})(?!\d)")

_liste = {"geholt": 0.0, "fehler": ""}


def quelle() -> str:
    """Einstellung → Umgebung → Katalogdienst aus `katalog_quelle` →
    veröffentlichte Datei."""
    q = (core.get_setting("cmf_quelle") or os.environ.get("CMF_QUELLE", "").strip())
    if q:
        return q
    kq = core.get_setting("katalog_quelle") or ""
    if "/api/" in kq:
        return kq.split("/api/", 1)[0] + "/api/cmf-codes.ndjson"
    return CMF_QUELLE


def schluessel(text: str) -> str:
    m = SCHLUESSEL.search(text or "")
    return m.group(1) if m else ""


def lesen(raw: bytes) -> tuple[str, str] | None:
    """(Schlüssel, Rohtext) des ersten Data-Matrix-Codes im Bild – oder None.

    Ohne zxing-cpp (fehlt das Paket) einfach None: Dann geht der Scan wie
    bisher zu Brickognize.
    """
    try:
        import zxingcpp
        from PIL import Image
    except Exception:
        return None
    try:
        bild = Image.open(io.BytesIO(raw))
        bild.load()
        if bild.mode not in ("L", "RGB"):
            bild = bild.convert("RGB")
        # Große Fotos verkleinern: Der Code wird auch bei 1600 px noch
        # gelesen, und das Lesen geht deutlich schneller.
        if max(bild.size) > 1600:
            bild.thumbnail((1600, 1600))
        for b in zxingcpp.read_barcodes(
                bild, formats=zxingcpp.BarcodeFormat.DataMatrix):
            k = schluessel(b.text)
            if k:
                return k, b.text[:200]
    except Exception:
        return None
    return None


def liste(erzwingen: bool = False) -> dict | None:
    """Die Code-Liste `{code: item_no}` – oder None, wenn nie eine zu
    bekommen war.

    Ist der Dienst gerade weg, gilt die zuletzt geholte aus `cmf_codes`.
    Wie in der iOS-App (0.51.0): Ohne je geladene Liste wird nicht einfach
    angelernt – sonst schlüge Gelerntes später bekannte Codes.
    """
    jetzt = time.time()
    if not erzwingen and jetzt - _liste["geholt"] < FRISCH_S:
        return _gespeichert()
    try:
        r = requests.get(quelle(), timeout=10,
                         headers={"User-Agent": integrations.USER_AGENT})
        r.raise_for_status()
        if len(r.content) > MAX_BYTES:
            raise ValueError("Code-Liste zu groß")
        codes = {}
        for zeile in r.text.splitlines():
            try:
                d = json.loads(zeile)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            code, nr = str(d.get("code") or ""), str(d.get("item_no") or "")
            if code.isdigit() and NUMMER.match(nr):
                codes[code] = nr.lower()
        if not codes:
            raise ValueError("Code-Liste leer")
        with core.db() as conn:
            conn.execute("DELETE FROM cmf_codes")
            conn.executemany("INSERT INTO cmf_codes (code, item_no) VALUES (?, ?)",
                             list(codes.items()))
        _liste.update(geholt=jetzt, fehler="")
        return codes
    except Exception as e:
        # Nicht bei jedem Scan erneut warten: Ein Fehlschlag zählt wie ein
        # Abruf, nur kürzer – nach fünf Minuten wird wieder gefragt.
        _liste.update(geholt=jetzt - FRISCH_S + 300, fehler=str(e)[:200])
        return _gespeichert()


def _gespeichert() -> dict | None:
    with core.db() as conn:
        codes = {r["code"]: r["item_no"]
                 for r in conn.execute("SELECT code, item_no FROM cmf_codes")}
    return codes or None


def nachschlagen(code: str, erzwingen: bool = False) -> dict:
    """`{code, item_no, quelle}` – quelle „gelernt“, „liste“ oder leer.
    `liste_fehlt`, wenn nie eine Liste zu bekommen war."""
    with core.db() as conn:
        r = conn.execute("SELECT item_no FROM cmf_gelernt WHERE code = ?",
                         (code,)).fetchone()
    if r:
        return {"code": code, "item_no": r["item_no"], "quelle": "gelernt"}
    codes = liste(erzwingen)
    if codes is None:
        return {"code": code, "item_no": "", "quelle": "", "liste_fehlt": True}
    nr = codes.get(code, "")
    return {"code": code, "item_no": nr, "quelle": "liste" if nr else ""}


def lernen(code: str, item_no: str, roh: str = "") -> dict:
    code = schluessel(code) or ""
    item_no = (item_no or "").strip().lower()
    if not code:
        raise ValueError("Kein gültiger Schachtel-Code")
    if not NUMMER.match(item_no):
        raise ValueError("Nur Sammelfiguren (col…) lassen sich zuordnen")
    with core.db() as conn:
        conn.execute(
            "INSERT INTO cmf_gelernt (code, item_no, roh, gelernt_am, gemeldet)"
            " VALUES (?, ?, ?, ?, 0) ON CONFLICT(code) DO UPDATE SET"
            " item_no = excluded.item_no, roh = excluded.roh,"
            " gelernt_am = excluded.gelernt_am, gemeldet = 0",
            (code, item_no, (roh or "")[:200], int(time.time())))
    gemeldet = melden(code, item_no) if teilen_an() else False
    return {"ok": True, "code": code, "item_no": item_no, "gemeldet": gemeldet}


def teilen_an() -> bool:
    return core.get_setting("cmf_teilen") == "1"


def melden(code: str, item_no: str) -> bool:
    """Nur Code und Figurnummer an den Hub – keine Namen, nichts sonst."""
    import hub
    try:
        hub._request("POST", hub.HUB_URL, "/v1/cmf/meldung",
                     token=core.get_setting("hub_token") or None,
                     body={"code": code, "item_no": item_no})
    except Exception:
        return False
    with core.db() as conn:
        conn.execute("UPDATE cmf_gelernt SET gemeldet = 1 WHERE code = ?", (code,))
    return True


def treffer(nr: str) -> dict:
    """Ein Scan-Treffer im Format von `integrations.recognize`."""
    name = ""
    with core.db() as conn:
        for tab, spalte in (("katalog_index", "item_no"),
                            ("collection", "item_id"), ("wanted", "item_id")):
            try:
                r = conn.execute(f"SELECT {spalte}, name FROM {tab} WHERE"
                                 f" lower({spalte}) = ? AND name != ''",
                                 (nr.lower(),)).fetchone()
            except Exception:
                r = None
            if r:
                nr, name = r[0], r[1]
                break
    return {"item_id": nr, "item_type": "minifig", "name": name or nr,
            "img_url": integrations.minifig_bild(nr), "score": 100,
            "category": "", "quelle": "schachtel",
            "bricklink_url": "https://www.bricklink.com/v2/catalog/"
                             "catalogitem.page?M=" + nr}
