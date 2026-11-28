"""Sammelfiguren-Schachteln am Data-Matrix-Code erkennen (cmf.py).

Der Code wird am Server gelesen, **bevor** Brickognize gefragt wird. Die
Liste kommt vom Katalogdienst, Gelerntes gilt vor ihr, und geteilt wird nur,
wenn der Schalter an ist – dann nur Code und Figurnummer.
"""
import io
import json
import time

import pytest
import zxingcpp
from PIL import Image

import cmf
import core
import hub
import integrations
import main
from fastapi.testclient import TestClient

ROH = "6472867 245S3 13916839 000341"
LISTE = "\n".join(json.dumps(z) for z in (
    {"code": "6472867", "item_no": "col427"},
    {"code": "6471959", "item_no": "col434"},
    {"code": "1234567", "item_no": "sw0001"},     # keine Sammelfigur: fällt raus
)) + "\n"


def _code_bild(text: str = ROH) -> bytes:
    b = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.DataMatrix)
    mv = memoryview(b.to_image(scale=8, add_quiet_zones=True))
    bild = Image.frombytes("L", (mv.shape[1], mv.shape[0]), mv.tobytes())
    puffer = io.BytesIO()
    bild.convert("RGB").save(puffer, "JPEG", quality=92)
    return puffer.getvalue()


class _Antwort:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status
        self.content = text.encode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise cmf.requests.HTTPError(str(self.status_code))


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "cmf.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer,"
            " created_at) VALUES ('anna', 'x', 1, 0, ?)",
            (int(time.time()),)).lastrowid
    abrufe, brickognize, meldungen = [], [], []
    zustand = {"liste": LISTE, "status": 200}

    def holen(url, **kw):
        abrufe.append(url)
        return _Antwort(zustand["liste"], zustand["status"])

    monkeypatch.setattr(cmf.requests, "get", holen)
    monkeypatch.setattr(cmf, "_liste", {"geholt": 0.0, "fehler": ""})
    monkeypatch.setattr(integrations, "recognize",
                        lambda raw: brickognize.append(1) or {"items": []})
    monkeypatch.setattr(hub, "_request",
                        lambda *a, **kw: meldungen.append((a, kw)) or {})
    main._scan_zeiten.clear()
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    return {"c": c, "abrufe": abrufe, "brickognize": brickognize,
            "meldungen": meldungen, "zustand": zustand}


def _scan(c, roh=ROH):
    return c.post("/api/scan", files={"file": ("s.jpg", _code_bild(roh),
                                               "image/jpeg")})


def test_schluessel_ist_erste_lange_ziffernfolge():
    assert cmf.schluessel(ROH) == "6472867"
    assert cmf.schluessel("245S3 6472867") == "6472867"
    assert cmf.schluessel("12345 245S3") == ""


def test_bekannter_code_ohne_brickognize(umgebung):
    r = _scan(umgebung["c"])
    assert r.status_code == 200
    d = r.json()
    assert d["items"][0]["item_id"] == "col427"
    assert d["items"][0]["quelle"] == "schachtel"
    assert d["schachtel"]["bekannt"] is True
    assert d["schachtel"]["quelle"] == "liste"
    assert umgebung["brickognize"] == []


def test_foto_ohne_code_geht_zu_brickognize(umgebung):
    puffer = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(puffer, "JPEG")
    r = umgebung["c"].post("/api/scan",
                           files={"file": ("s.jpg", puffer.getvalue(), "image/jpeg")})
    assert r.status_code == 200
    assert "schachtel" not in r.json()
    assert umgebung["brickognize"] == [1]


def test_liste_nimmt_nur_sammelfiguren(umgebung):
    codes = cmf.liste()
    assert codes == {"6472867": "col427", "6471959": "col434"}


def test_unbekannt_und_anlernen(umgebung):
    c = umgebung["c"]
    d = _scan(c, "7000001 245S3 13916839 000341").json()
    assert d["items"] == []
    assert d["schachtel"] == {"code": "7000001",
                              "roh": "7000001 245S3 13916839 000341",
                              "bekannt": False, "quelle": "",
                              "liste_fehlt": False}
    r = c.post("/api/cmf/lernen", json={"code": "7000001", "item_no": "COL430",
                                       "roh": "7000001 245S3"})
    assert r.status_code == 200
    assert r.json()["items"][0]["item_id"] == "col430"
    d = _scan(c, "7000001 245S3 13916839 000341").json()
    assert d["items"][0]["item_id"] == "col430"
    assert d["schachtel"]["quelle"] == "gelernt"
    assert umgebung["brickognize"] == []


def test_gelerntes_schlaegt_liste(umgebung):
    umgebung["c"].post("/api/cmf/lernen", json={"code": "6472867", "item_no": "col999"})
    assert _scan(umgebung["c"]).json()["items"][0]["item_id"] == "col999"


def test_nur_sammelfiguren_lassen_sich_anlernen(umgebung):
    c = umgebung["c"]
    for nr in ("sw0001", "75192-1", "col 1", "Col427'; --"):
        r = c.post("/api/cmf/lernen", json={"code": "7000001", "item_no": nr})
        assert r.status_code == 400, nr
    with core.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cmf_gelernt").fetchone()[0] == 0


def test_teilen_aus_meldet_nichts(umgebung):
    c = umgebung["c"]
    assert c.get("/api/config").json()["cmf_teilen"] is False
    r = c.post("/api/cmf/lernen", json={"code": "7000001", "item_no": "col430"})
    assert r.json()["gemeldet"] is False
    assert umgebung["meldungen"] == []


def test_teilen_an_meldet_nur_code_und_nummer(umgebung):
    c = umgebung["c"]
    with core.db() as conn:
        conn.execute("INSERT INTO collection (item_id, item_type, name,"
                     " added_at) VALUES ('col430', 'minifig', 'Geheimname', 0)")
    assert c.post("/api/settings/cmf_teilen", json={"an": True}).status_code == 200
    assert c.get("/api/config").json()["cmf_teilen"] is True
    r = c.post("/api/cmf/lernen", json={"code": "7000001 245S3", "item_no": "col430"})
    assert r.json()["gemeldet"] is True
    (args, kw), = umgebung["meldungen"]
    assert args[0] == "POST" and args[2] == "/v1/cmf/meldung"
    assert kw["body"] == {"code": "7000001", "item_no": "col430"}
    assert "Geheimname" not in json.dumps([args, kw], default=str)
    with core.db() as conn:
        assert conn.execute("SELECT gemeldet FROM cmf_gelernt").fetchone()[0] == 1


def test_teilen_nur_fuer_admin(umgebung):
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer,"
            " created_at) VALUES ('ben', 'x', 0, 0, 0)").lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "ben", False)
    assert c.post("/api/settings/cmf_teilen", json={"an": True}).status_code == 403
    assert not cmf.teilen_an()


def test_liste_nicht_erreichbar(umgebung):
    umgebung["zustand"]["status"] = 503
    d = _scan(umgebung["c"]).json()
    assert d["items"] == []
    assert d["schachtel"]["liste_fehlt"] is True
    assert umgebung["brickognize"] == []
    # Später wieder da: nachschlagen holt frisch, ohne neues Foto.
    umgebung["zustand"]["status"] = 200
    r = umgebung["c"].post("/api/cmf/nachschlagen", json={"code": "6472867"})
    assert r.json()["items"][0]["item_id"] == "col427"


def test_alte_liste_reicht_wenn_dienst_weg(umgebung):
    assert cmf.liste()["6472867"] == "col427"
    umgebung["zustand"]["status"] = 503
    d = umgebung["c"].post("/api/cmf/nachschlagen", json={"code": "6472867"}).json()
    assert d["items"][0]["item_id"] == "col427"
    assert d["schachtel"]["liste_fehlt"] is False


def test_liste_wird_nicht_bei_jedem_scan_geholt(umgebung):
    _scan(umgebung["c"])
    _scan(umgebung["c"], "6471959 245S3 13916839 000341")
    assert len(umgebung["abrufe"]) == 1


def test_quelle_aus_katalogdienst(umgebung, monkeypatch):
    monkeypatch.delenv("CMF_QUELLE", raising=False)
    core.set_setting("katalog_quelle", "http://192.168.0.199:8321/api/katalog.ndjson")
    assert cmf.quelle() == "http://192.168.0.199:8321/api/cmf-codes.ndjson"
    monkeypatch.setenv("CMF_QUELLE", "http://example.invalid/c.ndjson")
    assert cmf.quelle() == "http://example.invalid/c.ndjson"


def test_schachtel_zaehlt_nicht_gegen_bremse(umgebung):
    for _ in range(main.SCAN_MAX + 3):
        assert _scan(umgebung["c"]).status_code == 200
