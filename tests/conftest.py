"""Gemeinsame Test-Konfiguration.

Setzt Umgebungsvariablen, *bevor* das Backend importiert wird, damit weder
in ein echtes Datenverzeichnis (/data) geschrieben noch ein Secret erzeugt
wird. Danach ist `backend/main.py` importierbar.
"""
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time

# Muss vor dem Import von core/main gesetzt sein – core liest diese
# Variablen beim Modul-Import (SECRET_KEY_FILE, DB_PATH).
os.environ.setdefault("SECRET_KEY", "test-secret-do-not-use")
_TMP_DB = pathlib.Path(tempfile.gettempdir()) / "brickfolio-test.db"
os.environ.setdefault("DB_PATH", str(_TMP_DB))

_ROOT = pathlib.Path(__file__).resolve().parent.parent
# main.py mountet beim Import ein Static-Verzeichnis – auf das echte zeigen.
os.environ.setdefault("FRONTEND_DIR", str(_ROOT / "frontend"))

_BACKEND = _ROOT / "backend"
sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def kein_echter_preis_hub(monkeypatch):
    """Kein Test spricht mit dem echten Hub.

    Seit Teile- und Setdatei vom Hub kommen (3.9.25), legte jeder Test, der
    die Datei holt, ohne diese Sperre einen Preis-Zugang auf dem Live-Hub an
    – gemerkt am 17.10.2026 mit einer Netzsperre im Testlauf. Die Adresse
    zeigt deshalb ins Leere, und der Zugang ist ein fester Testwert.
    """
    monkeypatch.setenv("PREIS_HUB_URL", "http://hub.test")
    import hubpreise
    # Wer das Anlegen selbst prüft (test_hubpreise), holt sich das echte zurück.
    monkeypatch.setattr(hubpreise, "_echter_token", hubpreise._token, raising=False)
    monkeypatch.setattr(hubpreise, "_token", lambda: "prz_test")


@pytest.fixture(autouse=True)
def hintergrundlaeufe_abwarten():
    """Nach jedem Test warten, bis kein fremder Faden mehr läuft.

    **Warum das nötig ist.** `core.DB_PATH` ist ein Modulwert, und jeder
    Test biegt ihn auf eine frische Datei um. Ein Hintergrundlauf aus einem
    *früheren* Test liest ihn aber erst beim Schreiben – und schreibt
    damit in die Datenbank des Tests, der gerade läuft. Die Läufe sind
    `daemon`-Fäden, sie enden also nicht von selbst mit dem Test.

    Zweimal am 21.09.2026 in der Prüfung aufgeschlagen, beide Male in
    `test_katalog_hub.py` und beide Male auf einem Commit, der nur die
    Website berührte: einmal „assert 0 == 2" (die Figuren waren schon
    eingetragen), einmal „UNIQUE constraint failed: katalog_index.item_no"
    (die Zeile stand schon da). Beim erneuten Lauf war es grün – der
    klassische unzuverlässige Test.

    Bewusst **allgemein** gehalten: Es gibt acht Stellen, die Fäden
    starten, drei davon namenlos. Eine Liste davon veraltet; die Zahl der
    lebenden Fäden nicht.
    """
    vorher = {t.ident for t in threading.enumerate()}
    yield
    try:
        import main
        main._namen_lauf["stop"] = True
    except Exception:
        pass
    ende = time.time() + 5
    while time.time() < ende:
        rest = [t for t in threading.enumerate()
                if t.ident not in vorher and t.is_alive()]
        if not rest:
            return
        time.sleep(0.02)


@pytest.fixture
def conn():
    """Frische In-Memory-DB mit genau den Tabellen, die die Wertlogik liest."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE collection (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            condition TEXT NOT NULL DEFAULT 'used'
        );
        CREATE TABLE set_contents (
            set_no TEXT NOT NULL,
            fig_no TEXT NOT NULL,
            qty INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (set_no, fig_no)
        );
        """
    )
    yield c
    c.close()


def add_item(conn, item_id, item_type, quantity=1, condition="used"):
    """Fügt eine Sammlungszeile ein und gibt ihre id zurück."""
    cur = conn.execute(
        "INSERT INTO collection (item_id, item_type, quantity, condition) "
        "VALUES (?, ?, ?, ?)",
        (item_id, item_type, quantity, condition),
    )
    return cur.lastrowid


def add_contents(conn, set_no, fig_no, qty=1):
    conn.execute(
        "INSERT INTO set_contents (set_no, fig_no, qty) VALUES (?, ?, ?)",
        (set_no, fig_no, qty),
    )


@pytest.fixture
def mit_eigenem_schluessel(monkeypatch):
    """Für Tests, die den Weg über BrickLink prüfen.

    Seit 3.9.17 hängt die Preisquelle am eigenen Schlüssel (`hubpreise.an`).
    Ohne diese Festlegung entschied die gemeinsame Vorgabe-Datenbank: War sie
    noch leer, scheiterte `an()` an der fehlenden Tabelle und es ging zu
    BrickLink; hatte ein früherer Test sie eingerichtet, zum Preisdienst.
    Je nach Reihenfolge waren dieselben Tests grün oder rot (17.10.2026).
    """
    import hubpreise
    monkeypatch.setattr(hubpreise, "an", lambda: False)


@pytest.fixture(autouse=True)
def kein_preisverfall_nebenbei(monkeypatch):
    """Der Verfall vor Anfragen (`main.preise_verfallen_lassen`) läuft
    höchstens einmal je Minute – ob er in einem Test zuschlug, hing also an
    der Reihenfolge der Tests (test_wertverlust, 17.10.2026). In Tests nur,
    wo er gewollt ist: Wer ihn prüft, setzt `main._verfall_zuletzt[0] = 0`."""
    try:
        import main
    except Exception:
        return
    if hasattr(main, "_verfall_zuletzt"):    # manche Tests tauschen main aus
        monkeypatch.setattr(main, "_verfall_zuletzt", [float("inf")])
