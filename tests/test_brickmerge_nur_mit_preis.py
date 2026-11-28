"""Brickmerge nur mit Preis (13.10.2026)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")


def _html(d, an=True):
    if not shutil.which("node"):
        pytest.skip("node fehlt")
    fn = re.search(r"function setpreiseHtml\(d\) \{.*?\n\}\n", APP, re.S).group(0)
    js = ("const tr=(s,v={})=>s.replace(/\\{(\\w+)\\}/g,(_,k)=>v[k]);const esc=(s)=>String(s);"
          "const dateLocale=()=>'de-DE';const state={brickmergePreise:%s};" % ("true" if an else "false")
          + fn + "console.log(JSON.stringify(setpreiseHtml(%s)));" % json.dumps(d))
    return json.loads(subprocess.run(["node", "-e", js], capture_output=True,
                                     text=True, check=True).stdout)


def test_ohne_preis_kein_brickmerge():
    h = _html({"uvp_eur": 399.99, "brickmerge_url": "https://www.brickmerge.de/10221-1"})
    assert "Brickmerge" not in h and "UVP" in h


def test_mit_preis_steht_brickmerge():
    h = _html({"brickmerge_url": "https://www.brickmerge.de/75192-1", "ab_eur": 715.24})
    assert "Bei Brickmerge" in h and "ab " in h


def test_schalter_aus_kein_brickmerge():
    assert "Brickmerge" not in _html({"brickmerge_url": "x", "ab_eur": 5}, an=False)
