"""Der Satz für einen fehlenden Preis kommt an genau einer Stelle her und
nennt die Quelle: mit dem Preisdienst nie „bei BrickLink“ (10.10.2026)."""
import re
from pathlib import Path

APP = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")


def _helfer():
    m = re.search(r"function keinPreisSatz\(satzanfang\) \{.*?\n\}\n", APP, re.S)
    assert m, "keinPreisSatz fehlt"
    return m.group(0)


def test_bricklink_satz_nur_im_helfer():
    ohne = APP.replace(_helfer(), "")
    assert not re.search(r"[Kk]eine Preisdaten bei BrickLink", ohne), \
        "eine Ansicht verdrahtet den BrickLink-Satz fest – keinPreisSatz() nutzen"


def test_helfer_unterscheidet_die_quelle():
    h = _helfer()
    assert 'state.preisquelle === "hub"' in h
    assert "Noch kein Preis im Preisdienst." in h
    assert "noch kein Preis im Preisdienst" in h


def test_trefferkarte_und_einzelansicht_nutzen_den_helfer():
    assert APP.count("keinPreisSatz(false)") >= 1
    assert APP.count("keinPreisSatz(true)") >= 1


def test_auffrischen_nur_bei_bricklink():
    """Mit dem Preisdienst hat ↻ keine Wirkung – dann gibt es ihn nicht."""
    assert 'const auffrischen = hatPreise && state.preisquelle !== "hub";' in APP
    assert "${auffrischen ?" in APP
