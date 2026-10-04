"""Farben, die der BrickLink-Name Teil für Teil nennt (3.4.4).

**Der Vorfall:** Die Farbsuche schaute nur in die Farbliste des
Sehmodells. Eine Figur mit „Red Legs" im Namen fiel bei „rote Beine"
heraus, wenn das Modell Rot nicht zusammengefasst hatte – und „Red Torso,
Blue Legs" kam herein, weil Rot irgendwo stand. Gefunden beim Messen der
Bildmodelle gegen die Namen am 04.10.2026.
"""
import time

import pytest

import core
import main
import namensfarben
from fastapi.testclient import TestClient


def test_teil_und_farbe():
    assert namensfarben.teilfarben(
        "Explorer, Dark Tan Shirt, Tan Legs, Reddish Brown Hair") == {
        "torso": "dark tan", "legs": "tan", "hair": "reddish brown"}
    assert namensfarben.teilfarben("Knight - Black Hips and Red Legs") == {
        "legs": "red"}


def test_details_sind_keine_teile():
    """„Short Red Stripes" macht keinen roten Droiden (28.08.2026)."""
    assert namensfarben.teilfarben(
        "R5-D4 - Dome Head with Short Red Stripes") == {}
    assert "head" not in namensfarben.teilfarben("Stormtrooper, Black Head")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "namen.db"))
    core.init_db()
    now = int(time.time())
    with core.db() as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, is_dealer,"
            " created_at) VALUES ('anna', 'x', 1, 1, ?)", (now,))
    return TestClient(main.app)


def _katalog(zeilen):
    with core.db() as conn:
        for nr, name, farben in zeilen:
            conn.execute(
                "INSERT INTO katalog_index (item_no, item_type, name, such,"
                " woerter, farben, updated_at) VALUES (?, 'minifig', ?, ?, ?,"
                " ?, 0)",
                (nr, name, core.wortanfaenge(name)[0],
                 core.suchwoerter(name), farben))


def _nummern(begriff):
    return [t["item_id"] for t in main._katalog_lauf_suchen(begriff, 20,
                                                            "minifig")]


def test_rote_beine_laut_name_trotz_farbliste(client):
    """Die Farbliste kennt kein Rot – der Name sagt „Red Legs"."""
    _katalog([("g0001", "Castle Guard - Blue Torso, Red Legs", "blue, gray")])
    assert _nummern("red legs") == ["g0001"]


def test_anderes_teil_in_der_farbe_ist_kein_treffer(client):
    """Rot steht in der Farbliste, aber die **Beine** sind laut Name blau."""
    _katalog([("k0001", "Knight - Red Torso, Blue Legs", "red, blue"),
              ("k0002", "Knight - Blue Torso, Red Legs", "blue, red")])
    assert _nummern("red legs") == ["k0002"]


def test_der_name_entscheidet_beim_gefragten_teil(client):
    """Das Sehmodell fasst „rot" zusammen – der Name sagt, der Torso ist weiß."""
    _katalog([("d0001", "Droid - White Torso with Red Panels", "red"),
              ("d0002", "Droid - Red Torso", "white, red")])
    assert _nummern("red torso") == ["d0002"]


def test_ohne_teil_kommt_der_name_hinter_die_farbliste(client):
    """Ohne gefragtes Teil zählt die ganze Figur (`R-3PO` vor dem Droiden
    mit rotem Torso). Fehlt Rot in der Farbliste ganz, holt der Name die
    Figur trotzdem herein – bis 3.4.3 fiel sie heraus."""
    _katalog([("d0001", "Protocol Droid", "red"),
              ("d0002", "Battle Droid - Red Torso", "tan")])
    assert _nummern("red droid") == ["d0001", "d0002"]


def test_detail_im_namen_bleibt_draussen(client):
    """Wie vor 3.4.4: Rote Streifen machen keinen roten Droiden."""
    _katalog([("r0001", "R5-D4 - Dome Head with Short Red Stripes",
               "white, tan")])
    assert _nummern("red droid") == []
