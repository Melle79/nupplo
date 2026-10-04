"""Welche Farbe hat welches Teil – laut BrickLink-Namen?

**Warum.** Viele Namen sagen es Teil für Teil, in BrickLinks eigenen
Farbnamen: „Castle Guard - Red Legs, Blue Torso". Die Farbsuche schaute bis
3.4.3 dafür nur in die Farbliste des Sehmodells. Fehlte dort Rot, fiel die
Figur bei „rote Beine" heraus – obwohl der Name es wörtlich sagt.

**Nur „<Farbe> <Teil>".** Der Name nennt oft Details: „Dome Head with Short
Red Stripes" ist kein roter Droide (siehe `_farbrang`). Gelesen wird deshalb
nur, was unmittelbar vor einem Teilwort steht, und der Kopf gar nicht –
„Black Head" unter einem Helm sieht man auf keinem Bild.

**Bleibt lokal.** Die Namen holt jede Instanz über ihren eigenen
BrickLink-Zugang; weitergegeben wird nichts, also auch nicht, was hier
herausgelesen wird. Im veröffentlichten Index stehen weiter nur Nummer und
Bildbeschreibung.

Derselbe Leser misst im Katalogdienst (`namensmass.py`) die Bildmodelle.
"""
import re

FARBEN = sorted("""black|white|red|dark red|blue|dark blue|medium blue|bright light blue|
sand blue|dark azure|medium azure|light blue|green|dark green|sand green|bright green|lime|
olive green|yellow|bright light yellow|orange|dark orange|bright light orange|tan|dark tan|
reddish brown|dark brown|brown|nougat|light nougat|medium nougat|light bluish gray|
dark bluish gray|light gray|dark gray|pearl gold|flat silver|pearl light gray|pearl dark gray|
metallic silver|gold|silver|dark purple|purple|medium lavender|lavender|magenta|dark pink|
bright pink|pink|coral|light flesh|flesh|beige|gray""".replace("\n", "").split("|"),
                key=len, reverse=True)
_FARB_RE = "|".join(re.escape(f) for f in FARBEN)

# Teilwörter, wie BrickLink sie schreibt – und wie die Übersetzung sie aus
# „Beine", „Haare", „Umhang" macht. Bewusst eng: „Suit" ist meist der
# Torso, „Mask" nicht.
TEILE = {
    "legs": ("legs", "leg", "skirt"),
    "torso": ("torso", "jacket", "shirt", "vest", "top", "suit", "uniform",
              "robe", "dress", "sweater", "coat", "overalls"),
    "arms": ("arms", "sleeves"),
    "hair": ("hair",),
    "helmet": ("helmet", "hat", "cap", "hood", "cowl", "headdress", "crown",
               "bandana"),
    "cape": ("cape", "cloak"),
}
_MUSTER = {t: re.compile(r"\b(%s)\s+(?:[a-z-]+\s+){0,2}?(?:%s)\b"
                         % (_FARB_RE, "|".join(w)))
           for t, w in TEILE.items()}
_WORT_TEIL = {w: t for t, ws in TEILE.items() for w in ws}


def teilfarben(name: str) -> dict:
    """{'legs': 'red', 'torso': 'blue'} – nur was der Name eindeutig sagt.

    Je Aufzählungsglied höchstens ein Teil, und je Teil die erste Nennung:
    „Black Hips and Red Legs" sind rote Beine, nicht schwarze.
    """
    n = re.sub(r"\s+", " ", (name or "").lower().replace("grey", "gray"))
    aus = {}
    for glied in re.split(r"[,(;]| with | and ", n):
        for t, rx in _MUSTER.items():
            m = rx.search(glied)
            if m and t not in aus:
                aus[t] = m.group(1)
    return aus


def teile_in(woerter) -> set:
    """Welche Teile nennt eine (schon übersetzte) Anfrage? „red legs" → {legs}."""
    return {_WORT_TEIL[w] for w in woerter if w in _WORT_TEIL}
