"""Die Teiledatei kommt auch beim Öffnen und auf „Jetzt holen“.

Anlass, 17.10.2026: Ein in der Hub-Verwaltung eingetragenes Doppelgänger-
Paar stand nach zehn Minuten auf dem iPhone, in der SE erst nach bis zu
zwölf Stunden – die Teiledatei, die die Paare trägt, holte nur der
Hintergrundlauf. Katalog, Setnamen und Setdaten kamen beim Öffnen längst mit.
"""
import figurteile
import main


def _merken(monkeypatch):
    gerufen = []
    monkeypatch.setattr(figurteile, "ziehen", lambda: gerufen.append(1) or {"geholt": 0})
    for name in ("_katalog_ziehen", "_setnamen_ziehen", "_setdaten_ziehen"):
        monkeypatch.setattr(main, name, lambda: {"geholt": 0})
    return gerufen


def test_beim_oeffnen(monkeypatch):
    gerufen = _merken(monkeypatch)
    main._katalog_beim_oeffnen()
    assert gerufen == [1]


def test_von_hand(monkeypatch):
    gerufen = _merken(monkeypatch)
    monkeypatch.setattr(main.threading, "Thread", lambda **k: type("T", (), {"start": lambda s: None})())
    erg = main.katalog_holen_jetzt(user={"id": 1})
    assert gerufen == [1] and erg["figurteile"] == 0
