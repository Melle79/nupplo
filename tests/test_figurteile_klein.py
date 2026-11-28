"""Teiledatei mit kleinen Nummern (Katalogdienst 1.22.3): Nachschlagen ohne
Groß/klein, keine Doppelten (LLP002/llp002, 11.10.2026)."""
import json
import time

import pytest

import core
import figurteile


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "fk.db"))
    core.init_db()
    with core.db() as conn:
        figurteile.migrieren(conn)
        conn.execute("INSERT INTO figurteile (item_no, rb, teile, dg) VALUES (?, '', ?, ?)",
                     ("llp002", json.dumps([["3626px288", 3, 1], ["973", 1, 1]]),
                      json.dumps([{"item_no": "LLP002a", "hier": ["3626px288|3"], "dort": ["3626pb2199|3"]},
                                  {"item_no": "llp002a", "hier": ["3626px288|3"], "dort": ["3626pb2199|3"]},
                                  {"item_no": "llp002", "hier": [], "dort": []}])))
        conn.execute("INSERT INTO katalog_index (item_no, item_type, name, such, woerter, img_url,"
                     " farben, art, updated_at) VALUES ('LLP002a', 'minifig', 'Ninja', '', '', '', '', '', 1)")
    return conn


def test_grossgeschrieben_nachschlagen(db):
    d = figurteile.aus_datei("LLP002")
    assert d is not None and len(d["teile"]) == 2


def test_doppelgaenger_einmal_und_nicht_sich_selbst(db):
    d = figurteile.aus_datei("LLP002")
    assert [p["item_no"] for p in d["doppelgaenger"]] == ["LLP002a"]   # Katalog-Schreibweise
    assert d["doppelgaenger"][0]["name"] == "Ninja"


def test_anzahl_teile_ohne_gross_klein(db):
    with core.db() as conn:
        assert figurteile.anzahl_teile(conn, ["LLP002"]) == {"LLP002": 2}
