"""Prüffrage unter dem Treffer: kurzer Satz, Teilestreifen, Tipp auf die
Nummer übernimmt diese Figur (11.10.2026, sw0637/sw0637a)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")


def _frage(p):
    if not shutil.which("node"):
        pytest.skip("node fehlt")
    teile = [re.search(r"function %s\(.*?\n\}\n" % n, APP, re.S).group(0)
             for n in ("artName", "teilKurz", "dgFarbe", "dgUnterschiede", "dgNamen", "scanKurzFrage", "scanFragen")]
    konst = "".join(re.search(r"const %s = \{.*?\};" % k, APP, re.S).group(0)
                    for k in ("WELCHE", "AUFDRUCK"))
    konst += re.search(r"const REIHE = \[.*?\];", APP, re.S).group(0)
    aufruf = "scanFragen" if isinstance(p, list) else "scanKurzFrage"
    js = ("const lang='de';const tr=(s,v={})=>s.replace(/\\{(\\w+)\\}/g,(_,k)=>v[k]);"
          + "\n".join(teile) + konst
          + "console.log(JSON.stringify(%s(%s)));" % (aufruf, json.dumps(p)))
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True,
                                     text=True, check=True).stdout)


def test_zwei_koepfe():
    k = {"art": "kopf", "name": "Head Beard …"}
    assert _frage({"hier": [k], "dort": [k]}) == "Welcher Kopf? Davon hängt die Nummer ab."


def test_nur_auf_einer_seite():
    e = {"art": "sonst", "name_de": "Entfernungsmesser"}
    assert _frage({"hier": [], "dort": [e]}) == \
        "Mit oder ohne Entfernungsmesser? Davon hängt die Nummer ab."


def test_nummern_uebernehmen():
    i = APP.index("function scanDoppelgaenger(")
    s = APP[i:i + 4000]
    assert "dgTeileBilder(p, it.item_id, true)" in s
    assert "[data-dg-nehmen]" in s and 'card.querySelector("[data-add], [data-suggest]")?.click()' in s


def _t(nr, art, farbe, de):
    return {"item_id": nr, "art": art, "name_de": de, "color_name_de": farbe}


BEINE_GRAU = _t("970c00", "beine", "Hellgrau", "Beine")
TORSO_GRAU = _t("973pb0001c01", "torso", "Hellgrau", "Oberkörper")
HELM = _t("30380pb01", "kopfbedeckung", "Dunkelgrau", "Helm")
SW0002A = {"item_no": "sw0002a", "hier": [BEINE_GRAU, TORSO_GRAU],
           "dort": [dict(BEINE_GRAU, color_name_de="Hellblaugrau"),
                    dict(TORSO_GRAU, color_name_de="Hellblaugrau")]}
SW0002B = {"item_no": "sw0002b", "hier": [BEINE_GRAU, TORSO_GRAU, HELM],
           "dort": [dict(BEINE_GRAU, color_name_de="Hellblaugrau"),
                    dict(TORSO_GRAU, color_name_de="Hellblaugrau"),
                    dict(HELM, item_id="30380pb02")]}


def test_boba_fett_farbe_statt_mit_oder_ohne_beine():
    """Unter dem Scan-Treffer sw0002 stand zweimal „Mit oder ohne Beine?
    Davon hängt die Nummer ab.“ – sw0002a und sw0002b haben aber Beine,
    nur in Hellblaugrau statt Hellgrau (16.10.2026)."""
    assert _frage(SW0002A) == \
        "Oberkörper und Beine in Hellgrau oder Hellblaugrau? Davon hängt die Nummer ab."
    assert _frage(SW0002B) == \
        "Oberkörper und Beine in Hellgrau oder Hellblaugrau? Welcher Aufdruck auf der Kopfbedeckung?"


def test_anderer_aufdruck_ist_keine_mit_oder_ohne_frage():
    """Zwei Oberkörper derselben Form mit anderem Aufdruck (pb-Nummer)."""
    a = _t("973pb0001c01", "torso", "Weiß", "Oberkörper")
    assert _frage({"hier": [a], "dort": [dict(a, item_id="973pb0002c01")]}) == \
        "Welcher Aufdruck auf dem Oberkörper? Davon hängt die Nummer ab."


def test_zwei_gleiche_fragen_tragen_ihre_nummer():
    """Zwei Doppelgänger mit wortgleicher Frage untereinander sahen aus wie
    ein doppelter Eintrag (16.10.2026)."""
    k = {"art": "kopf", "name": "Head …"}
    a = {"item_no": "sw0001a", "hier": [k], "dort": [k]}
    b = {"item_no": "sw0001b", "hier": [k], "dort": [k]}
    assert _frage([a, b]) == ["sw0001a: Welcher Kopf? Davon hängt die Nummer ab.",
                              "sw0001b: Welcher Kopf? Davon hängt die Nummer ab."]
    assert _frage([SW0002A, SW0002B])[0].startswith("Oberkörper")


def test_boba_fett_mit_den_echten_teilenummern():
    """Nach der ersten Korrektur stand auf :8399 „Welche Beine und welcher
    Oberkörper?“ (Nupplo Test, 16.10.2026). In BrickLinks echten Listen
    heißen die Beine 970c09 und 970c86 – der Zusatz ist die Farbe der
    Beine, der Eintrag trägt die der Hüfte (Braun gegen Rotbraun). Die
    Oberkörper 973pb0282c01 und c03 unterscheiden sich in den Armen."""
    import figurteile
    farben = {"8": "Brown", "88": "Reddish Brown", "9": "Light Gray", "86": "Light Bluish Gray",
              "de": {"8": "Braun", "88": "Rotbraun", "9": "Hellgrau", "86": "Hellblaugrau"}}
    info = {"970c09": ("Hips and Light Gray Legs", "beine", "Beine"),
            "970c86": ("Hips and Light Bluish Gray Legs", "beine", "Beine"),
            "973pb0282c01": ("Torso SW Armor Boba Fett", "torso", "Oberkörper"),
            "973pb0282c03": ("Torso SW Armor Boba Fett", "torso", "Oberkörper")}
    def t(nr, f):
        return figurteile._teil(nr, f, 1, info, farben)
    p = {"item_no": "sw0002a", "hier": [t("970c09", 8), t("973pb0282c01", 9)],
         "dort": [t("970c86", 88), t("973pb0282c03", 86)]}
    assert _frage(p) == \
        "Oberkörper und Beine in Hellgrau oder Hellblaugrau? Davon hängt die Nummer ab."


def test_alter_helm_mit_px_ist_ein_aufdruck():
    """sw0002b trägt den Helm x50px3, sw0002 x50px1 – BrickLinks alte
    Schreibweise für Aufdrucke (iOS-App, 16.10.2026)."""
    h = _t("x50px1", "kopfbedeckung", "Dunkelgrau", "Helm")
    assert _frage({"hier": [h], "dort": [dict(h, item_id="x50px3")]}) == \
        "Welcher Aufdruck auf der Kopfbedeckung? Davon hängt die Nummer ab."


def test_scout_trooper_dreimal_anders_bedruckt():
    """sw0518 gegen sw1002 (17.10.2026): Helm, Oberkörper und Beine
    nur anders bedruckt. Dreimal „Welcher Aufdruck auf …“ hintereinander
    wäre ein Formular, keine Frage."""
    h = _t("15311pb01", "kopfbedeckung", "Olivgrün", "Helm")
    o = _t("973pb1591c01", "torso", "Olivgrün", "Oberkörper")
    b = _t("970c120pb06", "beine", "Olivgrün", "Beine")
    p = {"hier": [h, o, b], "dort": [dict(h, item_id="15311pb02"),
                                     dict(o, item_id="973pb3464c01"),
                                     dict(b, item_id="970c120pb11")]}
    assert _frage(p) == "Helm, Oberkörper und Beine: anderer Aufdruck. Davon hängt die Nummer ab."


def test_teile_von_oben_nach_unten():
    """Auf :8399 stand bei sw0518 „Helm, Beine und Oberkörper: anderer
    Aufdruck“ – in der Reihenfolge der Teileliste. iOS und die Figur selbst
    gehen von oben nach unten (Nupplo Test, 17.10.2026)."""
    h = _t("15311pb01", "kopfbedeckung", "Olivgrün", "Helm")
    o = _t("973pb1591c01", "torso", "Olivgrün", "Oberkörper")
    b = _t("970c120pb06", "beine", "Olivgrün", "Beine")
    p = {"hier": [b, h, o], "dort": [dict(b, item_id="970c120pb11"),
                                     dict(h, item_id="15311pb02"),
                                     dict(o, item_id="973pb3464c01")]}
    assert _frage(p) == "Helm, Oberkörper und Beine: anderer Aufdruck. Davon hängt die Nummer ab."
