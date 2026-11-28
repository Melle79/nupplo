"""SQLite im WAL-Modus – und was das für die Sicherungen heißt.

Vorfall (Performance-Durchsicht 09.10.2026): Bis 3.8.1 lief die Datenbank
mit Rücksprungjournal und `synchronous=FULL`. Schrieb ein Hintergrundlauf
(Katalog-Abzug mit 19.000 Zeilen, Namenslauf, Preise), standen lesende
Anfragen an; jeder kleine Commit kostete mehrere fsyncs (gemessen 0,83 ms
gegen 0,01 ms mit WAL, auf einer NAS-Festplatte 10–30 ms).

WAL bringt zwei Fallen mit, die hier festgehalten sind: `update.sh` kopiert
die Datei von außen, und `backup()` vererbt den WAL-Modus an die Kopie.
"""
import os
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

import core
import main


@pytest.fixture
def pfad(tmp_path, monkeypatch):
    p = str(tmp_path / "w.db")
    monkeypatch.setattr(core, "DB_PATH", p)
    core.init_db()
    return p


def test_die_datenbank_laeuft_im_wal_modus(pfad):
    with core.db() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_lesen_waehrend_geschrieben_wird(pfad):
    """Ein offener Schreibvorgang hält Leser nicht mehr auf."""
    schreiber = sqlite3.connect(pfad)
    schreiber.execute("BEGIN EXCLUSIVE")
    schreiber.execute("INSERT INTO settings (name, value) VALUES ('x', 'y')")
    try:
        leser = sqlite3.connect(pfad, timeout=0)
        leser.execute("SELECT COUNT(*) FROM users").fetchone()
        leser.close()
    finally:
        schreiber.rollback()
        schreiber.close()


def test_update_anfordern_leert_die_wal_datei(pfad, tmp_path):
    """Die update.sh bis 3.8.1 kopiert nur `brickfolio.db`. Was dann noch in
    der WAL-Datei stand, fehlte in der Sicherung vor dem Update."""
    # Eine offene Verbindung verhindert, dass SQLite beim Schließen der
    # letzten selbst zusammenführt – wie im laufenden Betrieb.
    offen = sqlite3.connect(pfad)
    offen.execute("SELECT COUNT(*) FROM users").fetchone()
    now = int(time.time())
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin,"
            " created_at) VALUES ('admin', 'x', 1, ?)", (now,)).lastrowid
    assert os.path.getsize(pfad + "-wal") > 0
    (tmp_path / "update-watch-alive").write_text("")
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "admin", True)
    assert c.post("/api/update/request", json={"delay": 60}).status_code == 200
    assert os.path.getsize(pfad + "-wal") == 0
    kopie = str(tmp_path / "kopie.db")
    with open(pfad, "rb") as q, open(kopie, "wb") as z:
        z.write(q.read())
    k = sqlite3.connect(kopie)
    assert k.execute("SELECT username FROM users").fetchone()[0] == "admin"
    k.close()
    offen.close()


def test_sicherungen_sind_einzelne_dateien(pfad, monkeypatch):
    """Sonst legt jedes Öffnen der Sicherung `-wal` und `-shm` daneben, und
    die bleiben liegen, wenn die Sicherung aufgeräumt wird."""
    monkeypatch.setattr(main, "BACKUP_KEEP", 3)
    main._auto_backup()
    bdir = os.path.join(os.path.dirname(pfad), "backups")
    (datei,) = [f for f in os.listdir(bdir)]
    s = sqlite3.connect(os.path.join(bdir, datei))
    assert s.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    s.close()
    assert os.listdir(bdir) == [datei]
