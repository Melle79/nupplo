"""Die Themenliste las den Kategoriebaum für jede Figur neu.

Vorfall (Performance-Durchsicht 09.10.2026): `_kategorien_lesen` hielt den
Baum nur für geladen, wenn er nicht leer war. Auf den meisten Instanzen hat
nie ein Admin den Baum geholt – die Tabelle ist leer, und jeder Aufruf ging
wieder in die Datenbank. `_thema_von` ruft ihn für jede Figur mit
Ziffernnummer (im echten Katalog ~1.270). Gemessen: 2.565 Abfragen und
1,2 s für `/api/katalog/liste/themen`, auf dem NAS mehrere Sekunden.
"""
import time

import pytest
from fastapi.testclient import TestClient

import core
import main


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "ke.db"))
    core.init_db()
    main._kategorien_vergessen()
    now = int(time.time())
    with core.db() as conn:
        uid = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin, created_at)"
            " VALUES ('anna', 'x', 1, ?)", (now,)).lastrowid
        # Figuren mit Ziffernnummer wie `22354` – kein Kürzel, also fragt
        # `_thema_von` den (leeren) Kategoriebaum.
        for i in range(40):
            nr = str(22300 + i)
            conn.execute(
                "INSERT INTO katalog_index (item_no, item_type, name, such,"
                " woerter, img_url, category_id, jahr, updated_at)"
                " VALUES (?, 'minifig', ?, ?, ?, '', '', 2020, ?)",
                (nr, "Figur " + nr, main._such_norm("Figur " + nr),
                 core.suchwoerter("Figur " + nr), now))
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(uid, "anna", True)
    return c


def test_leerer_baum_wird_nur_einmal_gelesen(ctx, monkeypatch):
    gelesen = []
    echt = core.db

    def zaehlend():
        gelesen.append(1)
        return echt()

    ctx.get("/api/katalog/liste/themen?art=minifig")   # lädt einmal
    monkeypatch.setattr(core, "db", zaehlend)
    for i in range(40):
        main._thema_von(str(22300 + i))
    assert gelesen == []


def test_nach_dem_holen_gilt_der_neue_baum(ctx):
    """Das Merkmal darf einen frisch geholten Baum nicht verdecken."""
    assert main._kategorie_oberthema("65") == ""
    with core.db() as conn:
        conn.execute("INSERT INTO katalog_kategorien (id, name, parent_id,"
                     " geholt_at) VALUES ('65', 'Star Wars', '', 0)")
    main._kategorien_vergessen()
    assert main._kategorie_oberthema("65") == "Star Wars"


def test_zweite_themenliste_rechnet_die_themen_nicht_neu(ctx, monkeypatch):
    """Vorfall (Nupplo Test, 09.10.2026): Auch mit gemerktem Baum brauchte
    die Themenliste auf dem NAS noch 0,8 s. Die Zeit ging in die Bereichs-
    regeln von `themes`, die für jede der 19.000 Nummern bei jedem Öffnen
    neu liefen, obwohl sich das Ergebnis nie ändert."""
    with core.db() as conn:
        for i in range(30):
            nr = "sw%04d" % (100 + i)
            conn.execute(
                "INSERT INTO katalog_index (item_no, item_type, name, such,"
                " woerter, img_url, category_id, jahr, updated_at)"
                " VALUES (?, 'minifig', ?, ?, ?, '', '', 2020, 0)",
                (nr, "Figur " + nr, main._such_norm("Figur " + nr),
                 core.suchwoerter("Figur " + nr)))
    ctx.get("/api/katalog/liste/themen?art=minifig")
    aufrufe = []
    echt = main.themes.from_minifig_number

    def zaehlend(nr):
        aufrufe.append(nr)
        return echt(nr)

    monkeypatch.setattr(main.themes, "from_minifig_number", zaehlend)
    r = ctx.get("/api/katalog/liste/themen?art=minifig")
    assert r.status_code == 200
    assert aufrufe == []


def test_eingespielte_sicherung_bringt_ihren_baum_mit(ctx):
    """Der gemerkte Baum darf einen zurückgespielten Tagesstand nicht
    überdecken – sonst stünde bis zum Neustart das alte Thema da."""
    import os
    import sqlite3
    bdir = os.path.join(os.path.dirname(core.DB_PATH), "backups")
    os.makedirs(bdir, exist_ok=True)
    stand = os.path.join(bdir, "brickfolio-20261009.db")
    with core.db() as conn:
        conn.execute("INSERT INTO katalog_kategorien (id, name, parent_id,"
                     " geholt_at) VALUES ('65', 'Star Wars', '', 0)")
    quelle = sqlite3.connect(core.DB_PATH)
    ziel = sqlite3.connect(stand)
    quelle.backup(ziel)
    ziel.close()
    with core.db() as conn:
        conn.execute("DELETE FROM katalog_kategorien")
    quelle.close()
    main._kategorien_vergessen()
    assert main._thema_von("22300", "65") == ""
    r = ctx.post("/api/backup_restore_file",
                 json={"name": "brickfolio-20261009.db"})
    assert r.status_code == 200, r.text
    assert main._thema_von("22300", "65") == "Star Wars"
