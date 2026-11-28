"""Die Katalogliste blättert in SQL, nicht in Python.

Vorfall (Performance-Durchsicht 09.10.2026): Ohne Thema lud jede Seite mit
60 Einträgen den ganzen Katalog – 19.000 Zeilen – und schnitt erst danach
zurecht. Gemessen 77 ms je Seite (jetzt 12 ms), auf dem NAS ein Vielfaches
bei jedem Weiterblättern.
"""
import contextlib
import time

import pytest
from fastapi.testclient import TestClient

import core
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "ks.db"))
    core.init_db()
    now = int(time.time())
    with core.db() as conn:
        conn.execute("INSERT INTO users (username, password_hash, is_admin,"
                     " created_at) VALUES ('anna', 'x', 1, ?)", (now,))
        for i in range(150):
            nr = f"sw{i:04d}"
            conn.execute(
                "INSERT INTO katalog_index (item_no, item_type, name, such,"
                " woerter, img_url, category_id, jahr, updated_at)"
                " VALUES (?, 'minifig', ?, ?, ?, '', '', 2020, ?)",
                (nr, "Figur " + nr, main._such_norm("Figur " + nr),
                 core.suchwoerter("Figur " + nr), now))
        conn.execute("INSERT INTO collection (item_id, item_type, name,"
                     " quantity, condition, added_at) VALUES"
                     " ('SW0070', 'minifig', 'x', 2, 'used', ?)", (now,))
        conn.execute("INSERT INTO wanted (item_id, item_type, name, added_at)"
                     " VALUES ('sw0071', 'minifig', 'x', ?)", (now,))
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(1, "anna", True)
    return c


def test_seiten_ergeben_zusammen_den_katalog(client):
    nummern = []
    for offset in (0, 60, 120, 180):
        d = client.get("/api/katalog/liste",
                       params={"offset": offset, "limit": 60}).json()
        assert d["gesamt"] == 150 and d["hat_katalog"]
        nummern += [e["item_no"] for e in d["eintraege"]]
        for e in d["eintraege"]:
            if e["item_no"] == "sw0070":
                assert e["besitz"] == 2      # Groß/klein egal, wie bisher
            if e["item_no"] == "sw0071":
                assert e["wunsch"] is True
    assert nummern == [f"sw{i:04d}" for i in range(150)]


def test_die_datenbank_liefert_nur_die_seite(client, monkeypatch):
    befehle = []
    echt = core.db

    @contextlib.contextmanager
    def mitschreibend():
        with echt() as conn:
            conn.set_trace_callback(befehle.append)
            yield conn

    monkeypatch.setattr(core, "db", mitschreibend)
    client.get("/api/katalog/liste", params={"offset": 60, "limit": 60})
    liste = [b for b in befehle if "FROM katalog_index" in b
             and "COUNT" not in b]
    assert liste and all("LIMIT" in b for b in liste)
