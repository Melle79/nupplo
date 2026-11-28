"""Figurenbilder von Rebrickable, wenn BrickLink nicht liefert.

Vorfall (21.11.2026): BrickLink fiel wieder aus. Auf Bildadressen kam eine
Wartungsseite, und jede Figur ohne abgelegtes Bild stand als Platzhalter da.
Rebrickable hat dieselben Figuren; die Nummer dort steht in der Teiledatei
(Feld `rb`). Abgelegt wird nur das echte Bild, unter seiner eigenen Adresse.
"""
import asyncio
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import core
import integrations
import main

BL = "https://img.bricklink.com/ItemImage/MN/0/sw0850.png"
RB = "https://cdn.rebrickable.com/media/sets/fig-001658.jpg"
BRICKOGNIZE = "https://storage.googleapis.com/brickognize-static/thumbnails/v2.22/fig/{}/0.webp"


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 800), (30, 30, 200)).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "r.db"))
    core.init_db()
    with core.db() as conn:
        conn.executemany("INSERT INTO figurteile (item_no, rb, teile, dg) VALUES (?, ?, '[]', '[]')",
                         [("sw0850", "fig-001658"),
                          # Zwei Figuren, ein Rebrickable-Bild: kein Rückfall.
                          ("sw0001a", "fig-000009"), ("sw0001b", "fig-000009")])
    main._bild_abrufe = asyncio.Semaphore(4)
    monkeypatch.setattr(main, "_bricklink_still", 0.0)
    monkeypatch.setattr(main, "_bl_rb_tabelle", {})
    abrufe = []
    zustand = {"bricklink": False}

    def holen(url, hosts=None):
        abrufe.append(url)
        if "bricklink" in url and not zustand["bricklink"]:
            return b"<html>Wartung</html>"
        return _jpeg()

    monkeypatch.setattr(integrations, "fetch_catalog_image", holen)
    return TestClient(main.app), abrufe, zustand


def test_figur_kommt_von_rebrickable(ctx):
    c, abrufe, _ = ctx
    r = c.get("/catalog", params={"u": BL})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "max-age=600"
    assert abrufe == [BL, RB]              # die Wartungsseite ist kein Bild: gleich weiter
    # Abgelegt ist nur das Rebrickable-Bild, unter seiner Adresse.
    assert main._katalog_bild(BL, holen=False) is None
    assert main._katalog_bild(RB, holen=False)


def test_waehrend_des_ausfalls_gleich_zu_rebrickable(ctx):
    c, abrufe, _ = ctx
    c.get("/catalog", params={"u": BL})
    abrufe.clear()
    assert c.get("/catalog", params={"u": BL}).status_code == 200
    assert abrufe == []                    # liegt da, BrickLink wird nicht gefragt


def test_mehrdeutige_nummer_nimmt_brickognize(ctx):
    c, abrufe, _ = ctx
    r = c.get("/catalog", params={"u": "https://img.bricklink.com/ItemImage/MN/0/sw0001a.png"})
    assert r.status_code == 200
    assert not any("rebrickable" in a for a in abrufe)
    assert abrufe[-1] == BRICKOGNIZE.format("sw0001a")


def test_ohne_rebrickable_nummer_brickognize(ctx):
    assert main._rebrickable_ersatz("https://img.bricklink.com/ML/Sw1132.jpg") == BRICKOGNIZE.format("sw1132")
    assert main._rebrickable_ersatz("https://img.bricklink.com/ItemImage/MN/0/eigene-figur.png") is None


def test_sets_von_rebrickable_teile_ohne_rueckfall(ctx):
    c, _, _ = ctx
    assert main._rebrickable_ersatz("https://img.bricklink.com/ItemImage/PN/11/4499.png") is None
    assert main._rebrickable_ersatz("https://img.bricklink.com/ItemImage/SN/0/75192-1.png") == \
        "https://cdn.rebrickable.com/media/sets/75192-1.jpg"
    assert main._rebrickable_ersatz("https://img.bricklink.com/SL/75222-1.jpg") == \
        "https://cdn.rebrickable.com/media/sets/75222-1.jpg"
    assert main._rebrickable_ersatz("https://img.bricklink.com/M/sw0850.gif") == RB
    # So steht es bei älteren Einträgen in `img_url` (21.11.2026 in der VM).
    assert main._rebrickable_ersatz("https://img.bricklink.com/ML/Sw0850.jpg") == RB


def test_liefert_bricklink_wieder_gilt_dessen_bild(ctx, monkeypatch):
    c, abrufe, zustand = ctx
    c.get("/catalog", params={"u": BL})
    zustand["bricklink"] = True
    monkeypatch.setattr(main, "_bricklink_still", 0.0)
    r = c.get("/catalog", params={"u": BL})
    assert r.status_code == 200 and "31536000" in r.headers["cache-control"]
    assert main._katalog_bild(BL, holen=False)
