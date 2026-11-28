"""Schwere Arbeit gehört nicht in die Ereignisschleife.

Vorfall (Performance-Durchsicht 09.10.2026): Der Import der BrickLink-
Katalogdatei (`/api/katalog/datei`, bis 40 MB XML, rund 19.000 Abfragen und
Schreibvorgänge) lief in einer `async`-Funktion direkt in der Schleife.
Solange er lief, beantwortete die Instanz gar nichts – keine Seite, kein
Bild, kein Nupplo Connect; auf einem NAS etliche Sekunden. Ebenso schrieb
`extern_beobachten` alle paar Minuten aus der Schleife in die Datenbank.
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import core
import main


def _in_der_schleife() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "ns.db"))
    core.init_db()
    with core.db() as conn:
        conn.execute("INSERT INTO users (username, password_hash, is_admin,"
                     " created_at) VALUES ('anna', 'x', 1, ?)",
                     (int(time.time()),))
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(1, "anna", True)
    return c


def test_der_katalogimport_laeuft_neben_der_schleife(client, monkeypatch):
    gesehen = []
    echt = core.suchwoerter

    def merkend(text):
        gesehen.append(_in_der_schleife())
        return echt(text)

    monkeypatch.setattr(core, "suchwoerter", merkend)
    xml = """<?xml version="1.0"?><CATALOG>
      <ITEM><ITEMTYPE>M</ITEMTYPE><ITEMID>sw0001a</ITEMID>
        <ITEMNAME>Battle Droid</ITEMNAME>
        <CATEGORY>65</CATEGORY><ITEMYEAR>1999</ITEMYEAR></ITEM>
    </CATALOG>"""
    r = client.post("/api/katalog/datei", content=xml.encode())
    assert r.status_code == 200, r.text
    assert gesehen and not any(gesehen)


def test_das_merken_des_externen_zugriffs_laeuft_neben_der_schleife(
        client, monkeypatch):
    gesehen = []
    monkeypatch.setattr(main, "_extern_art", lambda request: ("cloudflare", True))
    monkeypatch.setattr(main, "_extern_merken",
                        lambda weg, access: gesehen.append(_in_der_schleife()))
    client.get("/api/me")
    assert gesehen == [False]
