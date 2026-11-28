"""„Leicht zu verwechseln“: Jede Zeile beschreibt die genannte Figur, nicht
die offene (11.10.2026: unter sw1422 stand „Hier zusätzlich:
Entfernungsmesser“ – den hat aber sw1423)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")


def _wie(p):
    if not shutil.which("node"):
        pytest.skip("node fehlt")
    teile = [re.search(r"function %s\(.*?\n\}\n" % n, APP, re.S).group(0)
             for n in ("teilKurz", "dgTeile", "artName", "dgFarbe", "dgUnterschiede", "dgNamen", "dgKurzSatz", "dgTeileBilder", "doppelgaengerHtml")]
    js = ("const lang=\"de\";const tr=(s,v={})=>s.replace(/\\{(\\w+)\\}/g,(_,k)=>v[k]);"
          "const esc=(s)=>String(s);const imgSrc=(u)=>u;"
          "const ANDERES_TEIL={kopf:'Mit anderem Kopf',torso:'Mit anderem Oberkörper'};"
          + re.search(r"const REIHE = \[.*?\];", APP, re.S).group(0)
          + "\n".join(teile)
          + "const h=doppelgaengerHtml(%s, \"eigene\");"
            "console.log(JSON.stringify([h.match(/dg-wie\">([^<]*)</)[1],"
            "[...h.matchAll(/class=\"dg-teil( dg-ohne)?\"([^>]*)>/g)].map(m=>[!!m[1],(m[2].match(/&quot;figur&quot;:&quot;([^&]*)&quot;|\"figur\":\"([^\"]*)\"/)||[0,\"\",\"\"]).slice(1).join(\"\")]),"
            "[...h.matchAll(/dg-nr\">([^<]*)</g)].map(m=>m[1])]));" % json.dumps(p if isinstance(p, list) else [p]))
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True,
                                     text=True, check=True).stdout)


ENTF = {"item_id": "7218", "color_id": 86,
        "name": "Minifigure, Helmet Antenna / Rangefinder", "name_de": "Entfernungsmesser",
        "color_name": "Light Bluish Gray", "color_name_de": "Hellblaugrau"}


def test_im_steckbrief_von_sw1423_fehlt_sw1422_der_entfernungsmesser():
    wie, bilder, nrn = _wie({"item_no": "sw1422", "name": "…Nougat Head", "hier": [ENTF], "dort": []})
    assert wie == "Ohne Entfernungsmesser (Hellblaugrau)"
    # links das Teil der offenen Figur, rechts „ohne“ beim Doppelgänger
    # links das Teil der offenen Figur (öffnet groß), rechts „ohne“
    assert bilder == [[False, "eigene"], [True, ""]]
    assert nrn == ["eigene", "sw1422"]


def test_im_steckbrief_von_sw1422_hat_sw1423_ihn_zusaetzlich():
    wie, bilder, nrn = _wie({"item_no": "sw1423", "name": "…Rangefinder", "hier": [], "dort": [ENTF]})
    assert wie == "Zusätzlich mit Entfernungsmesser (Hellblaugrau)"
    assert [o for o, _ in bilder] == [True, False]


def test_statt_zeigt_beide_teile():
    kopf_a = {"item_id": "3626cpb1", "color_id": 3, "name": "Head A"}
    kopf_b = {"item_id": "3626cpb2", "color_id": 3, "name": "Head B"}
    wie, bilder, nrn = _wie({"item_no": "LLP002a", "name": "x", "hier": [kopf_a], "dort": [kopf_b]})
    assert wie.startswith("Mit Head B")
    assert [b for _, b in bilder] == ["eigene", "LLP002a"]
    assert nrn == ["eigene", "LLP002a"]


def test_gleiche_teileart_kurz():
    a = {"item_id": "x1", "color_id": 3, "name": "Head, Modified SW Yoda … White Hair", "art": "kopf",
         "color_name_de": "Sandgrün"}
    b = {"item_id": "x2", "color_id": 3, "name": "Head, Modified SW Yoda … Gray Hair", "art": "kopf",
         "color_name_de": "Sandgrün"}
    wie, _, _ = _wie({"item_no": "sw0446", "name": "Yoda", "hier": [a], "dort": [b]})
    assert wie == "Mit anderem Kopf"


def test_gleiche_teileart_andere_farbe():
    a = {"item_id": "973", "color_id": 1, "name": "Torso", "art": "torso", "color_name_de": "Beige"}
    b = {"item_id": "973", "color_id": 2, "name": "Torso", "art": "torso", "color_name_de": "Weiß"}
    wie, _, _ = _wie({"item_no": "sw0465", "name": "Yoda", "hier": [a], "dort": [b]})
    assert wie == "Mit Oberkörper (Weiß) statt (Beige)"


def test_doppelte_schreibweise_nur_einmal():
    if not shutil.which("node"):
        pytest.skip("node fehlt")
    d = {"name": "x", "hier": [], "dort": []}
    liste = [dict(d, item_no="LLP002a"), dict(d, item_no="llp002a"), dict(d, item_no="llp002")]
    fn = re.search(r"function doppelgaengerHtml\(.*?\n\}\n", APP, re.S).group(0)
    js = ("const lang='de';const tr=(s,v={})=>s.replace(/\\{(\\w+)\\}/g,(_,k)=>v[k]);"
          "const esc=(s)=>String(s);const imgSrc=(u)=>u;const dgTeileBilder=()=>'';const dgTeile=()=>({});"
          + fn + "const h=doppelgaengerHtml(%s,'LLP002');"
          "console.log((h.match(/data-info=/g)||[]).length);" % json.dumps(liste))
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "1"


def test_boba_fett_in_hellblaugrau_ist_eine_farbfrage():
    """sw0002a hat dieselben Beine und denselben Oberkörper wie sw0002, nur
    in Hellblaugrau. Im Steckbrief standen beide Teile doppelt mit Farbe
    (16.10.2026)."""
    def t(nr, art, farbe, de):
        return {"item_id": nr, "art": art, "name_de": de, "color_name_de": farbe}
    wie, _, _ = _wie({"item_no": "sw0002a", "name": "Boba Fett",
                      "hier": [t("970c00", "beine", "Hellgrau", "Beine"),
                               t("973pb0001c01", "torso", "Hellgrau", "Oberkörper")],
                      "dort": [t("970c00", "beine", "Hellblaugrau", "Beine"),
                               t("973pb0001c01", "torso", "Hellblaugrau", "Oberkörper")]})
    assert wie == "Oberkörper und Beine in Hellblaugrau statt Hellgrau"


def test_zwei_farbpaare_im_steckbrief_mit_komma():
    """Wie in der iOS-App (16.10.2026): Ändern sich zwei Teile in
    verschiedene Farben, steht jedes Paar für sich."""
    def t(nr, art, farbe, de):
        return {"item_id": nr, "art": art, "name_de": de, "color_name_de": farbe}
    wie, _, _ = _wie({"item_no": "x", "name": "x",
                      "hier": [t("970c00", "beine", "Hellgrau", "Beine"),
                               t("3626", "kopf", "Gelb", "Kopf")],
                      "dort": [t("970c00", "beine", "Hellblaugrau", "Beine"),
                               t("3626", "kopf", "Hellnougat", "Kopf")]})
    assert wie == "Kopf in Hellnougat statt Gelb, Beine in Hellblaugrau statt Hellgrau"
