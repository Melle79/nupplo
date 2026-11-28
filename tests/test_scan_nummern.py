"""Mehrfacherkennung: Jede Trefferkarte trägt die Nummer ihres Rahmens im
Foto (11.10.2026: bei 21 Figuren war die Liste unübersichtlich)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")


def test_karte_bekommt_rahmennummer():
    assert "function renderScanResults(items, nummern = null)" in APP
    assert 'class="scan-nr"' in APP and ".card-head .scan-nr" in CSS


def test_automatische_suche_nummeriert_wie_die_rahmen():
    # mehrfachRahmen nummeriert mit i + 1 – die Karten genauso.
    assert "renderScanResults(z.gefunden, z.gefunden.map((_, i) => i + 1));" in APP
    assert "d.dataset.nr = i + 1;" in APP


def test_von_hand_gezogene_rahmen_behalten_ihre_nummer():
    # Ein Rahmen ohne Treffer fällt weg – die übrigen behalten ihre Nummer.
    assert "nummern.push(i + 1);" in APP
    assert "renderScanResults(gefunden, nummern);" in APP


def test_einzeltreffer_ohne_nummer():
    assert "items.length < 2" in APP
