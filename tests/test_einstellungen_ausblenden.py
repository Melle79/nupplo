"""Einstellungen blenden aus, was im jeweiligen Zustand nichts bewirkt.

Anlass: Sven auf der Testinstanz (10.10.2026), ohne BrickLink-Schlüssel,
Preise vom Preisdienst. Die Karte „Katalog-Abzug“ sagte „Namen stehen nicht
darin. Die schlägt Nupplo mit deinem eigenen BrickLink-Zugang nach“ und bot
„Katalogdatei einlesen“ und „Kategorien holen“ an – obwohl Sets mit Name,
Thema und Jahr, deutsche Setnamen und die Teile jeder Figur längst aus dem
Katalog kommen und „Kategorien holen“ ohne Zugang nur einen Fehler bringt.
Seine Frage: „und den stellen wir ja mittlerweile zur Verfügung oder?“ –
dazu „abhängig von den Einstellungen einige Felder ausblenden“.
"""
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core
import figurteile
import integrations
import main

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "aus.db"))
    for name in integrations.SETTING_ENV.values():
        monkeypatch.delenv(name, raising=False)
    core.init_db()
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at)"
            " VALUES ('anna', 'x', 1, ?)", (int(time.time()),)).lastrowid
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    return c


def _funktion(name: str) -> str:
    js = (FRONTEND / "app.js").read_text()
    return re.search(r"function %s\([^)]*\) \{(.*?)\n\}" % name, js, re.S).group(1)


def test_stand_zaehlt_was_ohne_bricklink_mitkommt(client):
    """Die Karte muss wissen, ob Kategorien überhaupt fehlen. Sets aus der
    Setdatei tragen ihr Thema als `rb:` selbst; nur Sets aus BrickLinks
    Sets.xml hängen am Kategoriebaum."""
    with core.db() as conn:
        figurteile.migrieren(conn)
        conn.executemany(
            "INSERT INTO katalog_index (item_no, item_type, name, such,"
            " img_url, category_id, updated_at) VALUES (?, 'set', ?, '', '', ?, 0)",
            [("75192-1", "Falcon", "rb:Star Wars"),
             ("10179-1", "Falcon UCS", "65")])
        conn.execute("INSERT INTO set_daten (nr, name) VALUES ('75192-1', 'x')")
        conn.execute("INSERT INTO set_namen (nr, sprache, name, woerter)"
                     " VALUES ('75192-1', 'de', 'Millennium Falke', '')")
        conn.execute("INSERT INTO figurteile (item_no, teile) VALUES ('sw0001a', '[]')")
    d = client.get("/api/katalog/stand").json()
    assert (d["setdaten"], d["setnamen"], d["figurteile"]) == (1, 1, 1)
    assert d["sets_ohne_thema"] == 1
    with core.db() as conn:
        conn.execute("INSERT INTO katalog_kategorien (id, name, geholt_at)"
                     " VALUES ('65', 'Star Wars', 0)")
    assert client.get("/api/katalog/stand").json()["sets_ohne_thema"] == 0


def test_bricklink_wege_im_katalog_nur_wenn_noetig():
    """Die Katalogdatei nur, solange Figurennamen fehlen; „Kategorien holen“
    nur mit Zugang und nur, wenn ein Set sein Thema noch nicht kennt."""
    teil = _funktion("katalogWegeZeigen")
    assert "namen.hidden = !d.aktiv || !d.ohne_namen" in teil
    assert "kat.hidden = !d.hat_bricklink || !d.sets_ohne_thema" in teil
    html = (FRONTEND / "index.html").read_text()
    for block in ("katalog-namen-block", "katalog-kategorien-block"):
        assert f'<div id="{block}" hidden>' in html
    ohne_kommentare = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    assert "Namen stehen nicht darin" not in ohne_kommentare


def test_felder_ohne_wirkung_sind_versteckt():
    """Was im Zustand nichts tut, steht nicht da: der zweite Faktor ohne
    eingeschalteten Zugang, Modell und Test ohne Ollama-Adresse, der
    Cloudflare-Block mit Platzhaltern, Angebotspreise ohne Preisquelle."""
    html = (FRONTEND / "index.html").read_text()
    assert '<div id="connect-2fa-block" hidden>' in html
    assert '<div id="ollama-mehr" hidden>' in html
    assert '<div id="cf-ergebnis" hidden>' in html
    js = (FRONTEND / "app.js").read_text()
    assert '$("connect-2fa-block").hidden = !d.an;' in js
    assert '$("ollama-mehr").hidden = !da;' in _funktion("ollamaFelder")
    assert "fertig.hidden = !$(\"cf-host\").value.trim()" in _funktion("renderCfSnippet")
    # Seit 3.9.17 Angebote nur mit eigenem Schlüssel, nicht mit dem Preisdienst.
    h = _funktion("angeboteHinweis")
    assert 'const ohne = !state.bricklinkPrices || state.preisquelle === "hub";' in h
    # Seit 25.11.2026 bleibt der Schalter sichtbar und ist ohne Schlüssel
    # ausgegraut – man soll lesen, warum er nicht geht.
    assert '$("opt-angebote").disabled = ohne;' in h
