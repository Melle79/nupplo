"""Die Setbilder-Reihe lässt sich blättern: ‹ › an den Enden, Mausrad quer,
gewähltes Bild wird sichtbar (11.10.2026)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")


def test_reihe_bekommt_pfeile_an_allen_stellen():
    # Es gibt genau eine Stelle, die die Reihe baut – sie hängt die Hülle ein.
    assert APP.count('leiste.className = "sb-galerie";') == 1
    assert ".after(galerieBlaettern(leiste))" in APP


def test_pfeile_nur_wenn_es_weitergeht():
    assert "links.hidden = leiste.scrollLeft <= 2;" in APP
    assert "rechts.hidden = rest <= 2;" in APP
    assert ".sb-galerie-pfeil[hidden] { display: none; }" in CSS


def test_mausrad_blaettert_quer():
    assert 'leiste.addEventListener("wheel"' in APP and "passive: false" in APP


def test_grossansicht_nimmt_die_reihe_mit():
    i = APP.index("function renderGallery()")
    assert "galerieMarkieren(l, i)" in APP[i:i + 600]
