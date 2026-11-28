"""BrickLink-Verbrauch über alle Geräte geteilt (25.11.2026).

Mehrere Geräte mit demselben eigenen BrickLink-Schlüssel teilen sich
BrickLinks 5.000 Abrufe am Tag. iPhone und Mac sehen einander über iCloud;
Windows und alles andere über die SE: Jedes Gerät meldet beim Abgleich
seinen Tagesstand und bekommt zurück, was die anderen verbraucht haben.
Gemeinsam hören sie vor BrickLinks Grenze auf.
"""
import time

import pytest
from fastapi.testclient import TestClient

import core
import integrations
import main  # vor abgleich: main bindet es am Ende ein
import abgleich  # noqa: E402

ABDRUCK = "0123456789abcdef0123"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "v.db"))
    core.init_db()
    with core.db() as conn:
        conn.execute("INSERT INTO users (id, username, password_hash, is_admin, is_dealer, "
                     "created_at) VALUES (1, 'chefin', 'x', 1, 0, ?)", (int(time.time()),))


@pytest.fixture
def se(db):
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(1, "chefin", True)
    return c


def _melden(c, geraet, n, erschoepft=False, schluessel=ABDRUCK):
    r = c.post("/api/sync/bl-verbrauch", json={
        "tag": time.strftime("%Y-%m-%d"), "schluessel": schluessel,
        "geraet": geraet, "n": n, "erschoepft": erschoepft})
    assert r.status_code == 200, r.text
    return r.json()


def test_jedes_geraet_sieht_die_anderen(se):
    assert _melden(se, "iPhone", 300)["summe"] == 0
    r = _melden(se, "Windows-a1", 200)
    assert r["summe"] == 300 and [a["geraet"] for a in r["andere"]] == ["iPhone"]
    assert _melden(se, "iPhone", 350)["summe"] == 200


def test_stand_geht_nie_zurueck(se):
    """Gemeldet wird der Tagesstand. Kommt eine ältere Meldung zu spät an,
    bleibt der höhere."""
    _melden(se, "iPhone", 500)
    _melden(se, "iPhone", 100)
    assert _melden(se, "Mac", 0)["summe"] == 500


def test_anderer_schluessel_zaehlt_nicht_mit(se):
    _melden(se, "iPhone", 900, schluessel="ffffffffffffffffffff")
    assert _melden(se, "Mac", 10)["summe"] == 0


def test_brickLink_limit_eines_geraets_gilt_fuer_alle(se):
    _melden(se, "iPhone", 10, erschoepft=True)
    assert _melden(se, "Mac", 0)["erschoepft"] is True


def test_die_se_zaehlt_mit_wenn_sie_denselben_schluessel_hat(se, monkeypatch):
    monkeypatch.setattr(integrations, "schluessel_abdruck", lambda: ABDRUCK)
    core.set_setting("bl_verbrauch_tag", time.strftime("%Y-%m-%d"))
    core.set_setting("bl_verbrauch_n", "1200")
    r = _melden(se, "Windows-a1", 5)
    assert r["summe"] == 1200 and r["andere"][0]["geraet"] == "Nupplo SE"


def test_kaputte_meldung_wird_abgewiesen(se):
    r = se.post("/api/sync/bl-verbrauch", json={"tag": "gestern", "schluessel": ABDRUCK,
                                                "geraet": "x", "n": 1})
    assert r.status_code == 422


def test_abdruck_haengt_am_schluessel_und_verraet_ihn_nicht(db):
    assert integrations.schluessel_abdruck() == ""
    core.set_setting("bl_consumer_key", "CK-geheim")
    core.set_setting("bl_token", "TOK-geheim")
    a = integrations.schluessel_abdruck()
    assert len(a) == 20 and "geheim" not in a
    core.set_setting("bl_token", "anderer")
    assert integrations.schluessel_abdruck() != a


# ------------------------------------------------------------ am Gerät

class SE:
    """Die SE zu Hause, so wie der Abgleich sie anspricht."""

    def __init__(self, client):
        self.c, self.gemeldet = client, []

    def post(self, weg, daten):
        self.gemeldet.append((weg, daten))
        r = self.c.post(weg, json=daten)
        if r.status_code == 404:
            raise abgleich.AbgleichFehler("unbekannt")
        return r.json()


def test_geraet_meldet_und_nimmt_den_rest_mit(se, monkeypatch):
    import types
    import sync
    monkeypatch.setenv("NUPPLO_DESKTOP", "1")
    monkeypatch.setattr(integrations, "schluessel_abdruck", lambda: ABDRUCK)
    # Gerät und SE teilen sich im Test eine Datenbank – die SE soll hier
    # keinen eigenen Schlüssel haben, sonst zählte sie den Stand des Geräts.
    monkeypatch.setattr(sync, "integrations", types.SimpleNamespace(schluessel_abdruck=lambda: ""))
    _melden(se, "iPhone", 4400)
    core.set_setting("bl_verbrauch_tag", time.strftime("%Y-%m-%d"))
    core.set_setting("bl_verbrauch_n", "50")
    gegen = SE(se)
    abgleich._verbrauch_teilen(gegen)
    assert gegen.gemeldet[0][1]["n"] == 50
    stand = integrations.kontingent_stand()
    assert stand["geteilt"]["andere"] == 4400 and stand["frei"] == 50
    # Zusammen 4.500: dieses Gerät hört auf, obwohl es selbst erst 50 hat.
    core.set_setting("bl_verbrauch_n", "100")
    with pytest.raises(integrations.KontingentErschoepft):
        integrations._kontingent_vorher()


def test_ohne_schluessel_wird_nichts_gemeldet(se, monkeypatch):
    gegen = SE(se)
    abgleich._verbrauch_teilen(gegen)
    assert gegen.gemeldet == []


def test_aeltere_se_bricht_den_abgleich_nicht(db, monkeypatch):
    monkeypatch.setattr(integrations, "schluessel_abdruck", lambda: ABDRUCK)

    class Alt:
        def post(self, weg, daten):
            raise abgleich.AbgleichFehler("404")
    abgleich._verbrauch_teilen(Alt())
    assert integrations.andere_heute()["n"] == 0


def test_gestrige_meldung_zaehlt_heute_nicht(db):
    core.set_setting("bl_andere_tag", "2000-01-01")
    core.set_setting("bl_andere_n", "4999")
    assert integrations.andere_heute()["n"] == 0
