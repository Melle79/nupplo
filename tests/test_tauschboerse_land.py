"""Land an Angeboten in der Tauschbörse (Hub 1.30.0).

Anlass: 10.10.2026 – in der Tauschbörse fehlte das Land. Wer Porto und
Zoll sparen will, musste erst nachfragen, woher ein Angebot kommt. Der Hub ist
hier eine Attrappe; geprüft wird, was die Instanz ihm schickt und was sie aus
seiner Antwort macht.
"""
import time

import pytest
from fastapi.testclient import TestClient

import core
import hub
import main


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "c.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at) "
            "VALUES ('anna', 'x', 1, ?)", (int(time.time()),)).lastrowid
    core.set_setting("hub_token", "bft_test")
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    c.gesendet = []

    def put_profile(d):
        c.gesendet.append(d)
        return {**d, "member_id": "mem_ich"}
    monkeypatch.setattr(hub, "put_profile", put_profile)
    monkeypatch.setattr(hub, "profile", lambda m="": {"member_id": "mem_ich",
                                                       "country": ""})
    return c


def test_profil_ohne_land_loescht_das_gespeicherte_nicht(api):
    """Der Einrichtungsplaner speichert Schritt für Schritt, und der Schritt
    „Themen“ hat kein Landfeld. Ginge dabei `country: ""` mit, löschte jeder
    spätere Schritt das Land wieder, das man eben eingetragen hat."""
    api.put("/api/hub/profil", json={"about": "x", "themes": ["City"]})
    assert "country" not in api.gesendet[-1]


def test_land_geht_als_grosses_kuerzel_hinaus(api):
    api.put("/api/hub/profil", json={"country": "at"})
    assert api.gesendet[-1]["country"] == "AT"
    api.put("/api/hub/profil", json={"country": ""})
    assert api.gesendet[-1]["country"] == "", "leer heißt: Land löschen"


@pytest.mark.parametrize("gebiet, vorschlag", [
    ("DE", "DE"), ("europe", ""), ("", "")])
def test_land_vorschlag_aus_dem_preisgebiet(api, gebiet, vorschlag):
    """Das Preisgebiet ist oft schon das eigene Land – dann muss man es nicht
    zweimal suchen. Eine Region wie „Europa“ ist aber kein Land."""
    core.set_setting("price_region", gebiet)
    p = api.get("/api/hub/profil").json()
    assert p["land_vorschlag"] == vorschlag
    assert api.gesendet == [], "der Vorschlag allein schickt nichts zum Hub"


def test_filter_geht_an_den_hub_und_laender_kommen_zurueck(api, monkeypatch):
    gefragt = []

    def offers_mit_laendern(p=None):
        gefragt.append(p)
        return {"offers": [{"member_id": "mem_b", "item_id": "sw0001",
                            "name": "Luke", "country": "AT"}],
                "laender": [{"country": "AT", "continent": "europe",
                             "offers": 1}]}
    monkeypatch.setattr(hub, "offers_mit_laendern", offers_mit_laendern)
    d = api.get("/api/hub/offers?kontinent=europe").json()
    assert gefragt[-1]["kontinent"] == "europe"
    assert d["offers"][0]["country"] == "AT"
    assert d["laender"][0]["country"] == "AT"


def test_alter_hub_ohne_laender(api, monkeypatch):
    """Ein Hub vor 1.30.0 kennt `laender` nicht – die Liste muss trotzdem
    kommen, nur ohne Auswahl."""
    monkeypatch.setattr(hub, "offers_mit_laendern",
                        lambda p=None: {"offers": []})
    assert api.get("/api/hub/offers").json() == {"offers": [], "laender": []}


def test_leere_filter_gehen_nicht_als_parameter_hinaus(monkeypatch):
    """`?land=&kontinent=` an einen Hub 1.30.0 wäre harmlos, an einen älteren
    aber Rauschen in jedem Aufruf – und die Adresse ohne Filter muss die
    bisherige bleiben."""
    gerufen = []
    monkeypatch.setattr(hub, "_authed",
                        lambda m, pfad, **k: gerufen.append(pfad) or {})
    hub.offers({"q": "", "land": "", "kontinent": ""})
    hub.offers_mit_laendern({"q": "luke", "land": "AT"})
    assert gerufen == ["/v1/offers", "/v1/offers?q=luke&land=AT"]


def test_entdecken_traegt_das_land(api, monkeypatch):
    with core.db() as conn:
        conn.execute("INSERT INTO wanted (item_id, item_type, name, added_at) "
                     "VALUES ('sw0036', 'minifig', 'Trooper', ?)",
                     (int(time.time()),))
    monkeypatch.setattr(hub, "offers", lambda p=None: [
        {"member_id": "mem_b", "display_name": "Bruno", "item_id": "sw0036",
         "item_type": "minifig", "name": "Trooper", "country": "AT"}])
    monkeypatch.setattr(hub, "wants", lambda: [])
    monkeypatch.setattr(hub, "profiles", lambda: [])
    monkeypatch.setattr(hub, "put_wants", lambda l: {"count": 0})
    d = api.get("/api/hub/entdecken").json()
    assert d["hat"][0]["country"] == "AT"
