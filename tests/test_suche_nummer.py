"""Eine getippte Katalognummer findet die Figur bzw. das Set als ersten
Treffer – auch ohne BrickLink- und Rebrickable-Zugang."""
import time

import pytest

import core
import integrations
import main
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "nummer.db"))
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer, "
            "created_at) VALUES ('tester', 'x', 0, 0, ?)",
            (int(time.time()),)).lastrowid
        for no, typ, name in (
                ("sw1422", "minifig", "Clone Trooper, 327th Star Corps (Phase 2)"),
                ("sw1423", "minifig", "Clone Trooper, 327th Star Corps (Phase 2), Rangefinder"),
                ("75192-1", "set", "Millennium Falcon")):
            conn.execute(
                "INSERT INTO katalog_index (item_no, item_type, name, such, woerter,"
                " img_url, farben, art, updated_at) VALUES (?, ?, ?, '', ?, '', '', '', 1)",
                (no, typ, name, " " + name.lower() + " "))
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    monkeypatch.setattr(integrations, "rebrickable_enabled", lambda: False)
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "tester", False)
    return c


def suche(client, q, typ="minifig"):
    r = client.get("/api/search", params={"q": q, "item_type": typ})
    assert r.status_code == 200, r.text
    return r.json()


def test_figurennummer_ist_erster_treffer(client):
    d = suche(client, "sw1422")
    assert d["items"][0]["item_id"] == "sw1422"
    assert not d["eigene_leer"]


def test_grossschreibung_egal(client):
    assert suche(client, "SW1423")["items"][0]["item_id"] == "sw1423"


def test_setnummer_mit_und_ohne_variante(client):
    assert suche(client, "75192", "set")["items"][0]["item_id"] == "75192-1"
    assert suche(client, "75192-1", "set")["items"][0]["item_id"] == "75192-1"


def test_unbekannte_nummer_bleibt_leer(client):
    assert suche(client, "sw9999")["items"] == []


def test_wort_ist_keine_nummer(client):
    assert main._nummer_treffer("clone trooper", "minifig") is None


def test_setnummer_nur_einmal_auch_mit_setnamen(client, monkeypatch):
    monkeypatch.setattr(main, "_setnamen_suchen", lambda q: [
        {"item_id": "75192-1", "item_type": "set", "name": "Millennium Falke",
         "img_url": "", "sub": "", "year": 0, "bricklink_url": ""}])
    d = suche(client, "75192", "set")
    ids = [i["item_id"] for i in d["items"]]
    assert ids.count("75192-1") == 1
    assert d["items"][0]["name"] == "Millennium Falke", "der deutsche Name bleibt"


# ------------------------------------------- Treffer unter einem anderen Typ

def test_setname_bei_minifigur_kommt_unter_andere(client, monkeypatch):
    """„Supersternzerstörer“ bei Minifigur fand nichts (10.10.2026)."""
    monkeypatch.setattr(main, "_setnamen_suchen", lambda q, n=20: [
        {"item_id": "75457-1", "item_type": "set",
         "name": "Supersternzerstörer Executor", "img_url": "", "sub": "",
         "year": 0, "bricklink_url": ""}] if "stern" in q.lower() else [])
    d = suche(client, "Supersternzerstörer")
    assert d["items"] == []
    assert [(i["item_id"], i["item_type"]) for i in d["andere"]] == [("75457-1", "set")], \
        "keine Teile als Beifang"


def test_setnummer_bei_minifigur_kommt_unter_andere(client):
    d = suche(client, "75192")
    assert d["items"] == []
    assert d["andere"][0]["item_id"] == "75192-1"
    assert d["andere"][0]["item_type"] == "set"


def test_figurennummer_bei_set_kommt_unter_andere(client):
    d = suche(client, "sw1422", "set")
    assert [i["item_id"] for i in d["andere"]] == ["sw1422"]
    assert d["andere"][0]["item_type"] == "minifig"


def test_mit_treffern_kein_andere(client):
    assert "andere" not in suche(client, "sw1422")


def test_oberflaeche_zeigt_andere_und_stellt_den_typ_um():
    from pathlib import Path
    app = (Path(__file__).resolve().parent.parent / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "data.andere" in app and "andereTypenHinweis(andere)" in app
    # takeSuggestion stellt den Typ aus dem Treffer um
    assert 'if (it.item_type) $("m-type").value = it.item_type;' in app


# ------------------------------------------- Jahr und Teile an Set-Treffern

def test_settreffer_tragen_jahr_und_teile(client, monkeypatch):
    with core.db() as conn:
        conn.execute("INSERT INTO set_daten (nr, name, jahr, teile, thema, figuren)"
                     " VALUES ('75457-1', 'Executor', 2026, 6130, 'Star Wars', '[]')")
        conn.execute("INSERT INTO set_daten (nr, name, jahr, teile, thema, figuren)"
                     " VALUES ('75356-1', 'Executor', 2023, 630, 'Star Wars', '[]')")
    monkeypatch.setattr(main, "_setnamen_suchen", lambda q, n=20: [
        {"item_id": nr, "item_type": "set", "name": "Supersternzerstörer Executor",
         "img_url": "", "sub": "", "year": 0, "bricklink_url": ""}
        for nr in ("75457-1", "75356-1")])
    d = suche(client, "Supersternzerstörer", "set")
    assert [(i["item_id"], i["sub"]) for i in d["items"][:2]] == [
        ("75457-1", "2026 · 6.130 Teile"), ("75356-1", "2023 · 630 Teile")]
    # Auch unter „Als Set gefunden“ bei Typ Minifigur
    a = suche(client, "Supersternzerstörer")["andere"]
    assert a[0]["sub"] == "2026 · 6.130 Teile"


def test_esc_leert_suchfelder():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    app = (root / "frontend" / "app.js").read_text(encoding="utf-8")
    html = (root / "frontend" / "index.html").read_text(encoding="utf-8")
    assert 'ev.key !== "Escape"' in app
    for feld in ("search", "m-name", "hub-search", "kat-suche", "kat-themen-suche"):
        assert f'data-clear="{feld}"' in html, feld
