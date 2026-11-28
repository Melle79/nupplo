"""Teil für Teil: Bild und Name führen zu BrickLink (mit Farbe), nur das
Kästchen hakt ab (11.10.2026)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")


def test_zeile_ist_kein_label_mehr():
    # Seit 3.9.14 baut `bauListeHtml` die Zeilen – für die Wunschliste wie
    # für Suche und Steckbrief, damit beide gleich abhaken.
    i = APP.index("async function teilFuerTeil(")
    assert "bauListeHtml(teile, item.teile_da)" in APP[i:i + 1500]
    i = APP.index("function bauListeHtml(")
    stueck = APP[i:i + 4000]
    assert '<label class="fig-row bau-teil' not in stueck
    assert '<div class="fig-row bau-teil' in stueck
    assert 'class="bau-teil-link"' in stueck and 'target="_blank"' in stueck


def test_url_mit_farbe():
    if not shutil.which("node"):
        pytest.skip("node fehlt")
    fn = re.search(r"function teilBricklinkUrl\(t\) \{.*?\n\}\n", APP, re.S).group(0)
    fall = [{"item_id": "3626", "color_id": 11},
            {"item_id": "973pb0656c01", "color_id": 1,
             "bricklink_url": "https://www.bricklink.com/v2/catalog/catalogitem.page?P=973pb0656c01"},
            {"item_id": "x", "color_id": None}]
    js = fn + "console.log(JSON.stringify(%s.map(teilBricklinkUrl)));" % json.dumps(fall)
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True,
                                    text=True, check=True).stdout)
    assert out == [
        "https://www.bricklink.com/v2/catalog/catalogitem.page?P=3626&idColor=11",
        "https://www.bricklink.com/v2/catalog/catalogitem.page?P=973pb0656c01&idColor=1",
        "https://www.bricklink.com/v2/catalog/catalogitem.page?P=x"]
