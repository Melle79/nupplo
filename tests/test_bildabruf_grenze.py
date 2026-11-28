"""Neue Katalogbilder dürfen die Instanz nicht lahmlegen.

Vorfall (Performance-Durchsicht 09.10.2026): `/catalog` holte ein fehlendes
Bild synchron im Threadpool, bis zu 41 s je Bild und ohne Obergrenze. Eine
Seite mit 60 neuen Figuren belegte alle 40 Plätze, und jede andere Anfrage
der Instanz wartete mit. Dazu teilten sich zwei Fäden desselben Prozesses
eine Zwischendatei (`….<pid>.part`): Holten beide dasselbe Bild, fand der
zweite `os.replace` die Datei nicht mehr, und diese Anfrage bekam 404.
"""
import asyncio
import io
import threading
import time

import httpx
import pytest
from PIL import Image

import core
import integrations
import main


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 30, 30)).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def pfad(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "ba.db"))
    core.init_db()
    main._bild_abrufe = asyncio.Semaphore(4)


def test_hoechstens_vier_abrufe_gleichzeitig(pfad, monkeypatch):
    jetzt = {"n": 0, "max": 0}
    sperre = threading.Lock()
    roh = _jpeg()

    def langsam(url, hosts=None):
        with sperre:
            jetzt["n"] += 1
            jetzt["max"] = max(jetzt["max"], jetzt["n"])
        time.sleep(0.15)
        with sperre:
            jetzt["n"] -= 1
        return roh

    monkeypatch.setattr(integrations, "fetch_catalog_image", langsam)

    async def lauf():
        t = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=t, base_url="http://x") as c:
            antworten = await asyncio.gather(*[
                c.get("/catalog", params={
                    "u": f"https://img.bricklink.com/ItemImage/MN/0/sw{i:04d}.png",
                    "s": 160})
                for i in range(12)])
        return [a.status_code for a in antworten]

    codes = asyncio.run(lauf())
    assert codes == [200] * 12
    assert jetzt["max"] <= 4


def test_zwei_faeden_holen_dasselbe_bild(pfad, monkeypatch):
    roh = _jpeg()
    tor = threading.Barrier(2)

    def gleichzeitig(url, hosts=None):
        tor.wait(timeout=5)       # beide schreiben im selben Augenblick
        return roh

    monkeypatch.setattr(integrations, "fetch_catalog_image", gleichzeitig)
    url = "https://img.bricklink.com/ItemImage/MN/0/sw0001a.png"
    ergebnis = []
    faeden = [threading.Thread(target=lambda: ergebnis.append(
        main._katalog_bild(url))) for _ in range(2)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()
    assert len(ergebnis) == 2 and all(ergebnis)
