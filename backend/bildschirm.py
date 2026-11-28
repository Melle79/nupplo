"""Live-Scanner im Fenster von Nupplo für Windows (0.5.0).

Bis 0.4.6 war der Live-Scanner ein eigenes Programm in eigenem Fenster
(Tk, eigener Prozess), das sich bei der SE anmelden musste. 07.11.2026: „mir gefällt es am besten, wenn es wie in der Mac-App wäre.
Komplett integriert.“ In der Mac-App ist der Scanner eine Ansicht im
App-Fenster – also hier ein Bereich im Tab Scannen.

Was hier im Rücken passiert:

- Ein **Bildschirmfoto** des ganzen Desktops, auf dem man in der App den
  Bereich aufzieht (etwa das Bild eines Auktions-Streams).
- Ein **Wächter**, der diesen Bereich im Takt abfotografiert und auslöst,
  sobald dort etwas Neues still steht. Die Regeln sind die des bisherigen
  Scanners (`windows/livescan/livescan.py` bis 0.4.6), dort an vielen
  Abenden mit echten Streams eingestellt.
- Das ausgelöste Bild holt sich die App ab und schickt es durch denselben
  Weg wie ein hineingezogenes Foto. Erkennung, Treffer, Prüffrage und
  „In die Sammlung“ sind damit dieselben wie im Tab Scannen.

Nupplos eigenes Fenster soll dabei nicht mitfotografiert werden (auf dem Mac
lässt ScreenCaptureKit die eigenen Fenster weg). Das Windows-Programm setzt
dafür `FENSTER_AUSBLENDEN`; die SE selbst weiß nichts von Fenstern.

Nur im Desktop-Modus. Das Bild verlässt den Rechner nur so, wie es jedes
Foto im Tab Scannen tut.
"""
import io
import json
import logging
import sys
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

import abgleich
import core

router = APIRouter()
# Die Ursache (Treiber, Rechte, Bildschirm abgezogen) gehört ins Protokoll;
# die Oberfläche bekommt einen festen Satz, den sie übersetzen kann.
log = logging.getLogger("nupplo.bildschirm")

# Vom Windows-Programm gesetzt: `FENSTER_AUSBLENDEN(True)` nimmt Nupplos
# Fenster aus Bildschirmaufnahmen heraus, `(False)` wieder hinein. Nicht
# dauerhaft an, sonst fehlte Nupplo auch auf Bildschirmfotos, die man selbst
# macht (Sven schickt Fehler gern als Bildschirmfoto).
FENSTER_AUSBLENDEN = None

# --------------------------------------------------------------- Wächter
# Werte aus dem bisherigen Scanner, dort begründet.
FINGER_KANTE = 24          # ein Quadrat aus 24×24 Helligkeitswerten
TAKT = 0.6                 # Sekunden zwischen zwei Messungen
RUHE = 3                   # so viele Takte muss das Bild still stehen
PAUSE = 3.0                # Mindestabstand zweier Auslöser, in Sekunden
JE_MINUTE = 12             # Obergrenze an Auslösern je Minute
# Drehscheiben: Manche Verkäufer stellen die Figur auf einen Teller, der sich
# dauernd dreht – dann wird das Bild nie still. Hält die Bewegung an, ohne
# ein Sprung zu sein, wird mittendrin ausgelöst, höchstens ANSICHTEN Mal je
# Artikel.
DREH_TAKTE = 6
SPRUNG = 3.0               # ab diesem Vielfachen der Schwelle: neuer Artikel
ANSICHTEN = 4
# Ab welcher Abweichung „ruhig“ aufhört und „etwas Neues“ anfängt. Ein
# Bildschirmabzug ist pixelgenau, ein Videobild rauscht.
EMPFINDLICHKEIT = {
    "hoch": (1.0, 2.5),
    "mittel": (2.0, 5.0),
    "niedrig": (3.5, 10.0),
}


def fingerabdruck(bild) -> list:
    return list(bild.convert("L").resize((FINGER_KANTE, FINGER_KANTE)).getdata())


def abweichung(a, b) -> float:
    """Wie weit zwei Fingerabdrücke auseinanderliegen – 0 heißt gleich."""
    if not a or not b or len(a) != len(b):
        return 999.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


class Waechter:
    """Entscheidet Takt für Takt, ob ausgelöst wird. Ohne Faden und ohne
    Uhr von außen, damit sich die Regeln im Test Schritt für Schritt
    prüfen lassen."""

    def __init__(self, stufe: str = "mittel"):
        self.stufe = stufe if stufe in EMPFINDLICHKEIT else "mittel"
        self.letzter = None
        self.gesendet = None
        self.ruhe = 0
        # Beim ersten Takt gibt es nichts zu vergleichen, und das zählt als
        # Änderung: Wer einschaltet, während eine Figur hochgehalten wird,
        # will diese Figur.
        self.bewegt = 999.0
        self.dreht = 0
        self.ansichten = 0
        self.letzte_ausloesung = -1e9
        self.ausloesungen = []
        self.stand = "sieht zu"
        self.grund = ""

    def takt(self, abdruck: list, jetzt: float) -> bool:
        still, neu = EMPFINDLICHKEIT[self.stufe]
        d = abweichung(abdruck, self.letzter) if self.letzter else 999.0
        self.letzter = abdruck
        if d < still:
            self.ruhe += 1
            self.dreht = 0
        else:
            self.ruhe = 0
            self.bewegt = max(self.bewegt, d)
            if d > neu * SPRUNG:
                # Jemand greift ins Bild oder stellt etwas Neues hin.
                self.dreht = 0
                self.ansichten = 0
            else:
                self.dreht += 1
        self.ausloesungen = [t for t in self.ausloesungen if jetzt - t < 60]
        # `==`, nicht `>=`: genau in dem Takt, in dem die Ruhe eintritt –
        # sonst löste jedes weitere Stillstehen erneut aus.
        steht = self.ruhe == RUHE and self.bewegt >= neu
        kreist = bool(self.dreht) and self.dreht % DREH_TAKTE == 0
        grund = ""
        los = False
        if steht or kreist:
            if kreist and not steht and self.ansichten >= ANSICHTEN:
                grund = "genug Ansichten"
            elif self.gesendet and abweichung(abdruck, self.gesendet) < neu:
                # Ohne diese Regel schickte eine Figur, die eine Minute
                # stillgehalten wird, immer wieder dasselbe Bild.
                grund = "dasselbe Bild wie zuletzt"
            elif jetzt - self.letzte_ausloesung < PAUSE:
                grund = "Mindestabstand"
            elif len(self.ausloesungen) >= JE_MINUTE:
                grund = "Obergrenze je Minute erreicht"
            else:
                self.gesendet = abdruck
                self.letzte_ausloesung = jetzt
                self.bewegt = 0.0
                self.ansichten += 1
                self.ausloesungen.append(jetzt)
                los = True
        self.stand = "still" if self.ruhe else "dreht sich" if self.dreht else "sieht zu"
        self.grund = grund
        return los


# --------------------------------------------------------------- Aufnahme
def _ursprung() -> tuple[int, int]:
    """Linke obere Ecke des ganzen Desktops. Ein zweiter Bildschirm links
    vom ersten hat negative Koordinaten; `ImageGrab.grab(all_screens=True)`
    liefert das Bild ab dieser Ecke, ein `bbox` dagegen in Desktop-
    Koordinaten."""
    if sys.platform == "win32":
        import ctypes
        m = ctypes.windll.user32.GetSystemMetrics
        return m(76), m(77)            # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
    return 0, 0


def _greifen(bereich: tuple | None = None):
    """Bildschirm (oder ein Bereich davon, x, y, Breite, Höhe) als PIL-Bild.
    Im Test ersetzt."""
    from PIL import ImageGrab
    if bereich:
        x, y, b, h = bereich
        return ImageGrab.grab(bbox=(x, y, x + b, y + h), all_screens=True)
    return ImageGrab.grab(all_screens=True)


def verfuegbar() -> bool:
    # Bildschirmaufnahme gibt es mit Pillow nur unter Windows und macOS.
    # Auf dem NAS (Linux, ohne Bildschirm) taucht der Bereich gar nicht auf.
    return sys.platform in ("win32", "darwin")


class _OhneFenster:
    """Für die Dauer einer Aufnahme Nupplos Fenster ausblenden."""

    def __enter__(self):
        if FENSTER_AUSBLENDEN:
            try:
                FENSTER_AUSBLENDEN(True)
            except Exception:
                pass

    def __exit__(self, *_):
        if FENSTER_AUSBLENDEN and not _zustand["an"]:
            try:
                FENSTER_AUSBLENDEN(False)
            except Exception:
                pass


def _png(bild) -> bytes:
    raus = io.BytesIO()
    bild.convert("RGB").save(raus, format="PNG")
    return raus.getvalue()


# --------------------------------------------------------------- Zustand
_sperre = threading.Lock()
_zustand = {"an": False, "nummer": 0, "bild": b"", "stand": "", "grund": "", "fehler": ""}
_faden = None


def _bereich() -> tuple | None:
    try:
        b = json.loads(core.get_setting("bildschirm_bereich") or "null")
        return tuple(int(v) for v in b) if b and len(b) == 4 else None
    except (ValueError, TypeError):
        return None


def _ausgeloest(bild):
    with _sperre:
        _zustand["nummer"] += 1
        _zustand["bild"] = _png(bild)


def _wachen(waechter: Waechter, bereich: tuple):
    while _zustand["an"]:
        beginn = time.monotonic()
        try:
            with _OhneFenster():
                bild = _greifen(bereich)
        except Exception as e:
            log.warning("Aufnahme des Bereichs fehlgeschlagen: %s", e)
            _zustand.update(an=False, fehler="Der Bereich lässt sich nicht aufnehmen.")
            break
        # Das ausgelöste Bild ist genau dieser Abzug, kein zweiter: Der
        # Augenblick, auf den es ankommt, ist der, in dem es still stand.
        if waechter.takt(fingerabdruck(bild), beginn):
            _ausgeloest(bild)
        _zustand.update(stand=waechter.stand, grund=waechter.grund)
        rest = TAKT - (time.monotonic() - beginn)
        if rest > 0:
            time.sleep(rest)
    if FENSTER_AUSBLENDEN:
        try:
            FENSTER_AUSBLENDEN(False)
        except Exception:
            pass


def anhalten():
    global _faden
    _zustand["an"] = False
    if _faden is not None:
        _faden.join(5)
        _faden = None


# --------------------------------------------------------------- Endpunkte
def _pruefen():
    abgleich._nur_desktop()
    if not verfuegbar():
        raise HTTPException(404, "Nicht gefunden")


@router.get("/api/bildschirm")
def bildschirm_stand(user: dict = Depends(abgleich._admin())):
    abgleich._nur_desktop()
    return {"verfuegbar": verfuegbar(), "bereich": _bereich(), "an": _zustand["an"],
            "stufe": core.get_setting("bildschirm_stufe") or "mittel",
            "nummer": _zustand["nummer"], "stand": _zustand["stand"], "grund": _zustand["grund"],
            "fehler": _zustand["fehler"]}


@router.get("/api/bildschirm/foto")
def bildschirm_foto(user: dict = Depends(abgleich._admin())):
    """Der ganze Desktop zum Aufziehen des Bereichs. Verkleinert, sonst
    gingen bei zwei 4K-Bildschirmen etliche MB durch die Leitung; der
    Maßstab steht im Kopf, damit die App zurückrechnen kann."""
    _pruefen()
    try:
        with _OhneFenster():
            bild = _greifen()
    except Exception as e:
        log.warning("Bildschirmfoto fehlgeschlagen: %s", e)
        raise HTTPException(500, "Der Bildschirm lässt sich nicht aufnehmen.")
    massstab = min(1.0, 1600 / max(1, bild.width))
    if massstab < 1:
        bild = bild.resize((round(bild.width * massstab), round(bild.height * massstab)))
    x, y = _ursprung()
    return Response(_png(bild), media_type="image/png", headers={
        "X-Ursprung": f"{x},{y}", "X-Massstab": f"{massstab:.6f}", "Cache-Control": "no-store"})


class Bereich(BaseModel):
    x: int
    y: int
    b: int = Field(ge=16, le=20000)
    h: int = Field(ge=16, le=20000)


@router.post("/api/bildschirm/bereich")
def bildschirm_bereich(body: Bereich, user: dict = Depends(abgleich._admin())):
    _pruefen()
    an = _zustand["an"]
    anhalten()
    core.set_setting("bildschirm_bereich", json.dumps([body.x, body.y, body.b, body.h]))
    if an:
        _starten()
    return {"bereich": _bereich()}


class Wachen(BaseModel):
    an: bool
    stufe: str = "mittel"


def _starten():
    global _faden
    bereich = _bereich()
    if not bereich:
        raise HTTPException(400, "Bitte zuerst einen Bereich wählen.")
    _zustand.update(an=True, fehler="", stand="sieht zu")
    _faden = threading.Thread(target=_wachen, name="bildschirm",
                              args=(Waechter(core.get_setting("bildschirm_stufe") or "mittel"), bereich),
                              daemon=True)
    _faden.start()


@router.post("/api/bildschirm/wachen")
def bildschirm_wachen(body: Wachen, user: dict = Depends(abgleich._admin())):
    _pruefen()
    if body.stufe in EMPFINDLICHKEIT:
        core.set_setting("bildschirm_stufe", body.stufe)
    anhalten()
    if body.an:
        _starten()
    return bildschirm_stand(user)


@router.post("/api/bildschirm/jetzt")
def bildschirm_jetzt(user: dict = Depends(abgleich._admin())):
    """Ohne Automatik: den Bereich einmal aufnehmen und erkennen lassen."""
    _pruefen()
    bereich = _bereich()
    if not bereich:
        raise HTTPException(400, "Bitte zuerst einen Bereich wählen.")
    try:
        with _OhneFenster():
            bild = _greifen(bereich)
    except Exception as e:
        log.warning("Aufnahme des Bereichs fehlgeschlagen: %s", e)
        raise HTTPException(500, "Der Bereich lässt sich nicht aufnehmen.")
    _ausgeloest(bild)
    return {"nummer": _zustand["nummer"]}


@router.get("/api/bildschirm/bild")
def bildschirm_bild(user: dict = Depends(abgleich._admin())):
    _pruefen()
    with _sperre:
        nummer, bild = _zustand["nummer"], _zustand["bild"]
    if not bild:
        raise HTTPException(404, "Noch kein Bild")
    return Response(bild, media_type="image/png",
                    headers={"X-Nummer": str(nummer), "Cache-Control": "no-store"})
