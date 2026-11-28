"""Rahmen der Mehrfacherkennung in Lesereihenfolge: Zeile für Zeile von
oben, in der Zeile von links (11.10.2026: „im Bild durcheinander“)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")


def _folge(boxen):
    if not shutil.which("node"):
        pytest.skip("node fehlt")
    fn = re.search(r"function leseReihenfolge\(boxen\) \{.*?\n\}\n", APP, re.S).group(0)
    js = fn + "console.log(JSON.stringify(leseReihenfolge(%s)));" % json.dumps(boxen)
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True,
                                     text=True, check=True).stdout)


def test_zwei_reihen_auf_dem_regal():
    # Obere Reihe leicht versetzt (y 100/112/95), untere bei y 300.
    b = [{"x": 300, "y": 112, "w": 50, "h": 100},   # 0 oben Mitte
         {"x": 50, "y": 305, "w": 50, "h": 100},    # 1 unten links
         {"x": 500, "y": 95, "w": 50, "h": 100},    # 2 oben rechts
         {"x": 60, "y": 100, "w": 50, "h": 100},    # 3 oben links
         {"x": 400, "y": 290, "w": 50, "h": 100}]   # 4 unten rechts
    assert _folge(b) == [3, 0, 2, 1, 4]


def test_einzelne_reihe_von_links():
    b = [{"x": x, "y": 10 + (i % 2) * 20, "w": 40, "h": 80}
         for i, x in enumerate([400, 100, 250])]
    assert _folge(b) == [1, 2, 0]


def test_wird_vor_dem_nummerieren_angewandt():
    i = APP.index("const folge = leseReihenfolge(z.treffer);")
    assert i < APP.index("mehrfachRahmen(z.treffer);", i)
