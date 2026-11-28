"""Tageslimit je Gerät in Nupplo für Windows (25.11.2026).

Anlass: Zwei Geräte mit demselben BrickLink-Schlüssel teilen sich die
5.000 Abrufe am Tag, ohne voneinander zu wissen (Konzept vom 06.11.2026,
Sven: „Alles ja!“). Jedes Gerät zählt deshalb selbst und hört bei seinem
Limit auf; meldet BrickLink das Limit, ist bis morgen Schluss. Die SE auf
einem Server zählt mit, begrenzt aber nicht.
"""
import pytest

import core
import integrations


class Antwort:
    def __init__(self, status=200, code=200):
        self.status_code = status
        self._code = code

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise integrations.requests.HTTPError(str(self.status_code))

    def json(self):
        return {"meta": {"code": self._code, "message": "x"},
                "data": {"avg_price": "1.0", "unit_quantity": 1}}


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "l.db"))
    core.init_db()


@pytest.fixture
def antworten(monkeypatch):
    liste = []
    monkeypatch.setattr(integrations.requests, "get",
                        lambda *a, **k: liste.pop(0) if liste else Antwort())
    return liste


def _abruf():
    return integrations._price_request("MINIFIG", "sw0001", "U", "", None)


def test_auf_dem_server_gezaehlt_aber_nicht_begrenzt(db, antworten, monkeypatch):
    """Seit dem geteilten Verbrauch (25.11.2026) zählt auch die SE am Server
    – sie meldet ihren Teil den Geräten –, hört aber nie von selbst auf."""
    monkeypatch.delenv("NUPPLO_DESKTOP", raising=False)
    core.set_setting("bl_geraet_limit", "100")
    for _ in range(101):
        _abruf()
    assert integrations.verbrauch_heute() == 101


def test_geraet_zaehlt_und_hoert_beim_limit_auf(db, antworten, monkeypatch):
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    core.set_setting("bl_geraet_limit", "100")
    for _ in range(100):
        _abruf()
    assert integrations.verbrauch_heute() == 100
    with pytest.raises(integrations.KontingentErschoepft):
        _abruf()
    assert integrations.kontingent_stand()["frei"] == 0


def test_vorgabe_ist_2000(db):
    assert integrations.geraet_limit() == 2000


def test_bricklink_limit_gilt_bis_morgen(db, antworten, monkeypatch):
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    antworten.append(Antwort(status=429))
    with pytest.raises(integrations.KontingentErschoepft):
        _abruf()
    assert integrations.kontingent_stand()["bricklink_limit"]
    with pytest.raises(integrations.KontingentErschoepft):
        _abruf()
    # Der gescheiterte zählt mit, der abgewiesene nicht.
    assert integrations.verbrauch_heute() == 1


def test_fehlgeschlagener_abruf_zaehlt(db, monkeypatch):
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")

    def kaputt(*a, **k):
        raise integrations.requests.ConnectionError("weg")
    monkeypatch.setattr(integrations.requests, "get", kaputt)
    with pytest.raises(integrations.requests.ConnectionError):
        _abruf()
    assert integrations.verbrauch_heute() == 1
