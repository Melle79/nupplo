"""Teileanzahl, Themenwelt und Jahr im Set-Steckbrief.

Anlass (10.10.2026): Sven wollte bei Sets sehen, was jede Händlerseite zeigt
– Teile, Themenwelt, Erscheinungsjahr. Die Instanz wusste davon nur Jahr und
Thema, beides über BrickLink, und die Teileanzahl gar nicht. Der
Katalogdienst legt die Angaben aus Rebrickables Downloads als
`setdaten.ndjson` neben den Abzug; die Instanz fragt dafür keinen Schlüssel.
"""
import json
import time
from pathlib import Path

import pytest
import requests

import core
import main
from fastapi.testclient import TestClient

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "sd.db"))
    core.init_db()
    with core.db() as conn:
        conn.execute("INSERT INTO users (username, password_hash, is_admin,"
                     " is_dealer, created_at) VALUES ('anna', 'x', 1, 1, ?)",
                     (int(time.time()),))
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + core.create_token(1, "anna", True)
    return c


def _datei(monkeypatch, zeilen, status=200, etag='"d1"', roh=""):
    inhalt = roh + "".join(json.dumps(z, ensure_ascii=False) + "\n"
                           for z in zeilen)

    class Fake:
        status_code = status
        headers = {"ETag": etag} if etag else {}
        content = inhalt.encode()
        text = inhalt

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

    gefragt = []
    monkeypatch.setattr(main.requests, "get",
                        lambda url, **kw: gefragt.append((url, kw)) or Fake())
    return gefragt


# Wie die echte Datei vom 10.10.2026 (Katalogdienst 1.20.0), gekürzt
DATEN = [{"fig": "fig-000997", "name": "Han Solo", "teile": 4},
         {"figuren": [["fig-000997", 1], ["fig-001419", 2]], "jahr": 2000,
          "name": "Millennium Falcon", "nr": "7190-1", "teile": 671,
          "thema": "Star Wars"},
         {"jahr": 2017, "name": "Millennium Falcon", "nr": "75192-1",
          "teile": 7541, "thema": "Star Wars",
          "unterthema": "Ultimate Collector Series"}]


def test_steckbrief_bekommt_teile_figuren_jahr_und_thema(client, monkeypatch):
    _datei(monkeypatch, DATEN)
    assert main._setdaten_ziehen()["geholt"] == 2
    d = client.get("/api/setdaten/7190-1").json()
    assert d == {"nr": "7190-1", "bekannt": True, "name": "Millennium Falcon",
                 "jahr": 2000, "teile": 671, "thema": "Star Wars",
                 "unterthema": "", "figuren": 3, "erschienen": "", "eol": "",
                 "eol_geschaetzt": False, "uvp_eur": None, "name_de": "", "bilder": []}
    d = client.get("/api/setdaten/75192-1").json()
    assert d["unterthema"] == "Ultimate Collector Series"
    assert d["figuren"] == 0


def test_nummer_ohne_fassung(client, monkeypatch):
    """Von Hand erfasste Sets stehen oft als „7190“ da, ohne „-1“."""
    _datei(monkeypatch, DATEN)
    main._setdaten_ziehen()
    assert client.get("/api/setdaten/7190").json()["teile"] == 671


def test_unbekanntes_set_ist_kein_fehler(client, monkeypatch):
    _datei(monkeypatch, DATEN)
    main._setdaten_ziehen()
    r = client.get("/api/setdaten/custom-123")
    assert r.status_code == 200
    assert r.json()["bekannt"] is False


def test_kaputte_datei_loescht_nichts(client, monkeypatch):
    """Eine leere oder abgeschnittene Antwort darf den Steckbrief nicht
    leeren – sonst verschwänden die Angaben bis zum nächsten Abruf."""
    _datei(monkeypatch, DATEN)
    main._setdaten_ziehen()
    _datei(monkeypatch, [], roh="<html>Wartung</html>\n", etag='"d2"')
    assert main._setdaten_ziehen()["grund"] == "leer"
    assert client.get("/api/setdaten/7190-1").json()["bekannt"] is True


def test_schiefe_zeile_kostet_nur_sich_selbst(client, monkeypatch):
    _datei(monkeypatch, DATEN + [{"nr": "1-1", "teile": "viele"}])
    assert main._setdaten_ziehen()["geholt"] == 2


def test_etag_und_quelle_aus_der_umgebung(client, monkeypatch):
    """Die Testinstanz holt die Datei vom Probe-Katalogdienst im LAN, bis
    sie veröffentlicht ist."""
    monkeypatch.setenv("SETDATEN_QUELLE", "http://probe:8321/api/setdaten.ndjson")
    gefragt = _datei(monkeypatch, DATEN)
    main._setdaten_ziehen()
    _datei(monkeypatch, DATEN, status=304)
    gefragt2 = _datei(monkeypatch, [], status=304)
    assert main._setdaten_ziehen()["grund"] == "unverändert"
    assert gefragt[0][0] == "http://probe:8321/api/setdaten.ndjson"
    assert gefragt2[0][1]["headers"]["If-None-Match"] == '"d1"'


def test_keine_datei_ist_kein_fehler(client, monkeypatch):
    _datei(monkeypatch, [], status=404, etag=None)
    assert main._setdaten_ziehen()["grund"] == "noch keine Datei"


def test_oberflaeche_fragt_setdaten_im_steckbrief_und_im_katalog():
    """Die Python-Tests führen app.js nicht aus; sie sichern nur, dass der
    Abruf an beiden Stellen verdrahtet ist und die Wörter übersetzt sind."""
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert js.count("setdatenEinsetzen(") == 4      # Definition + 3 Aufrufe
    en = json.loads((FRONTEND / "i18n" / "en.json").read_text(encoding="utf-8"))
    for wort in ("{n} Teile", "{n} Figuren", "erschienen {datum}"):
        assert wort in en


# --------------------------------------------------- Enthaltene Figuren

import integrations  # noqa: E402

# 75455-1: im August bei BrickLink ohne Figuren gespeichert, Rebrickable
# kennt Boba Fett. sw1538 ↔ fig-014801 steht in der Teiledatei; die zweite
# Figur hat dort kein eindeutiges Gegenstück.
FIG_DATEN = [{"fig": "fig-014801", "name": "Boba Fett", "teile": 6},
             {"fig": "fig-099999", "name": "Unbekannter Droide", "teile": 3},
             {"figuren": [["fig-014801", 1]], "jahr": 2025, "name": "Boba Fett",
              "nr": "75455-1", "teile": 1200, "thema": "Star Wars"},
             {"figuren": [["fig-014801", 1], ["fig-099999", 2]], "jahr": 2025,
              "name": "Gemischt", "nr": "75999-1", "teile": 300,
              "thema": "Star Wars"},
             {"jahr": 2025, "name": "Ohne Figuren", "nr": "40917-1",
              "teile": 80, "thema": "Seasonal"}]


def _teiledatei(paare):
    with core.db() as conn:
        for no, rb in paare:
            conn.execute("INSERT INTO figurteile (item_no, rb, teile)"
                         " VALUES (?, ?, '[]')", (no, rb))


def _gespeichert(nr):
    with core.db() as conn:
        return [(r["fig_no"], r["qty"]) for r in conn.execute(
            "SELECT fig_no, qty FROM set_contents WHERE set_no = ?", (nr,))]


def test_figuren_ohne_bricklink_aus_der_setdatei(client, monkeypatch):
    """Anlass: „Enthaltene Figuren“ blieb ohne eigenen BrickLink-Zugang
    leer, und bei 75455-1 fehlte Boba Fett (10.10.2026)."""
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _teiledatei([("sw1538", "fig-014801")])
    _datei(monkeypatch, FIG_DATEN)
    main._setdaten_ziehen()
    d = client.get("/api/set_figs/75455-1").json()
    assert d["quelle"] == "katalog"
    assert [(f["item_id"], f["qty"]) for f in d["items"]] == [("sw1538", 1)]
    assert d["items"][0]["img_url"].endswith("/sw1538.png")
    # Vollständig abgebildet → gespeichert, damit „👥 1/1“ stimmt
    assert _gespeichert("75455-1") == [("sw1538", 1)]


def test_ohne_abbildung_nur_zeigen_nicht_speichern(client, monkeypatch):
    """Eine fig-Nummer in set_contents brächte „👥 3/4“ und die Fehlliste
    durcheinander, die mit BrickLink-Nummern der Sammlung rechnen."""
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _teiledatei([("sw1538", "fig-014801")])
    _datei(monkeypatch, FIG_DATEN)
    main._setdaten_ziehen()
    d = client.get("/api/set_figs/75999-1").json()
    assert [f["item_id"] for f in d["items"]] == ["sw1538", "fig-099999"]
    assert d["items"][1]["name"] == "Unbekannter Droide"
    assert "cdn.rebrickable.com" in d["items"][1]["img_url"]
    assert _gespeichert("75999-1") == []


def test_set_ohne_figuren_ist_eine_antwort(client, monkeypatch):
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _datei(monkeypatch, FIG_DATEN)
    main._setdaten_ziehen()
    d = client.get("/api/set_figs/40917-1").json()
    assert d["items"] == [] and "unbekannt" not in d


def test_bricklink_leer_aber_katalog_weiss_mehr(client, monkeypatch):
    """BrickLink führt neue Sets oft erst Wochen später mit Figuren."""
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    monkeypatch.setattr(integrations, "bricklink_subsets", lambda nr: [])
    _teiledatei([("sw1538", "fig-014801")])
    _datei(monkeypatch, FIG_DATEN)
    main._setdaten_ziehen()
    d = client.get("/api/set_figs/75455-1").json()
    assert d["quelle"] == "katalog"
    assert [f["item_id"] for f in d["items"]] == ["sw1538"]


def test_leere_gespeicherte_inhalte_werden_nachgefuellt(client, monkeypatch):
    """Auf der Testinstanz standen 16 eigene Sets mit leerem Inhalt da,
    darunter 75455-1 (10.10.2026). Nach dem Holen der Setdatei sind die,
    deren Figuren alle eine BrickLink-Nummer haben, gefüllt."""
    _teiledatei([("sw1538", "fig-014801")])
    with core.db() as conn:
        for nr in ("75455-1", "75999-1", "40917-1"):
            conn.execute("INSERT INTO collection (item_id, item_type, name,"
                         " added_at) VALUES (?, 'set', ?, ?)",
                         (nr, nr, int(time.time())))
            conn.execute("INSERT INTO set_meta (set_no, figs_fetched_at)"
                         " VALUES (?, 1)", (nr,))
    _datei(monkeypatch, FIG_DATEN)
    assert main._setdaten_ziehen()["setinhalte"] == 1
    assert _gespeichert("75455-1") == [("sw1538", 1)]
    assert _gespeichert("75999-1") == []          # nicht ganz abgebildet
    assert _gespeichert("40917-1") == []          # hat zu Recht keine


def test_sets_kommen_mit_der_setdatei_in_den_katalog(client, monkeypatch):
    """Anlass: Der nächtliche Abzug brachte nur Figuren, Sets kamen nur
    über eine von Hand geladene Sets.xml – „die Sets sind ja auch wichtig“
    (10.10.2026). Was schon von BrickLink da ist, bleibt."""
    with core.db() as conn:
        conn.execute("INSERT INTO katalog_index (item_no, item_type, name,"
                     " category_id, jahr, updated_at) VALUES ('7190-1', 'set',"
                     " 'Millennium Falcon (BL)', '9999999', 2000, 1)")
    _datei(monkeypatch, DATEN + [{"jahr": 2024, "name": "Visual Dictionary",
                                  "nr": "5008259-1", "teile": 0}])
    assert main._setdaten_ziehen()["katalog_neu"] == 1
    with core.db() as conn:
        rows = {r["item_no"]: r for r in conn.execute(
            "SELECT * FROM katalog_index WHERE item_type = 'set'")}
    assert set(rows) == {"7190-1", "75192-1"}          # ohne Buch
    assert rows["7190-1"]["name"] == "Millennium Falcon (BL)"
    assert rows["75192-1"]["jahr"] == 2017
    d = client.get("/api/katalog/liste/themen", params={"art": "set"}).json()
    assert d["arten"]["set"] == 2
    assert "Star Wars" in [t["thema"] for t in d["themen"]]
    liste = client.get("/api/katalog/liste", params={
        "art": "set", "thema": "Star Wars"}).json()
    # 7190-1 steht unter seiner BrickLink-Kategorie, nicht unter dem
    # Rebrickable-Thema – die Zeile bleibt, wie die Sets.xml sie brachte.
    assert [e["item_no"] for e in liste["eintraege"]] == ["75192-1"]


def test_erscheinung_und_eol_aus_der_setdatei(client, monkeypatch):
    """Anlass: „erscheinung und eol sind schon sehr wichtig“ (10.10.2026). Der Katalogdienst holt beides von Brickset; was nicht wie
    ein Monat aussieht, kommt nicht in die Oberfläche."""
    _datei(monkeypatch, [
        dict(DATEN[1], erschienen="2000-07", eol="2002-02"),
        dict(DATEN[2], erschienen="2017-10", eol="<b>bald</b>")])
    main._setdaten_ziehen()
    d = client.get("/api/setdaten/7190-1").json()
    assert (d["erschienen"], d["eol"]) == ("2000-07", "2002-02")
    d = client.get("/api/setdaten/75192-1").json()
    assert (d["erschienen"], d["eol"]) == ("2017-10", "")


def test_eol_als_jahr_geschaetzt_und_uvp(client, monkeypatch):
    """Das abgestimmte Format (10.10.2026): `eol` auch nur als Jahr, „≈“
    über `eol_geschaetzt`, `uvp_eur` in Euro."""
    _datei(monkeypatch, [
        dict(DATEN[1], eol="2028", eol_geschaetzt=True, uvp_eur=749.99),
        dict(DATEN[2], eol="bald", eol_geschaetzt="ja", uvp_eur="teuer")])
    main._setdaten_ziehen()
    d = client.get("/api/setdaten/7190-1").json()
    assert (d["eol"], d["eol_geschaetzt"], d["uvp_eur"]) == ("2028", True, 749.99)
    d = client.get("/api/setdaten/75192-1").json()
    assert (d["eol"], d["eol_geschaetzt"], d["uvp_eur"]) == ("", False, None)


def test_alte_tabelle_bekommt_die_spalten(tmp_path, monkeypatch):
    """Auf der Testinstanz kann set_daten schon ohne die beiden Spalten
    stehen (Patch 0001 vor 0003)."""
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "alt.db"))
    import sqlite3
    with sqlite3.connect(core.DB_PATH) as conn:
        conn.execute("CREATE TABLE set_daten (nr TEXT PRIMARY KEY, name TEXT"
                     " NOT NULL DEFAULT '', jahr INTEGER NOT NULL DEFAULT 0,"
                     " teile INTEGER NOT NULL DEFAULT 0, thema TEXT NOT NULL"
                     " DEFAULT '', unterthema TEXT NOT NULL DEFAULT '',"
                     " figuren TEXT NOT NULL DEFAULT '[]')")
    core.init_db()
    with core.db() as conn:
        spalten = {r[1] for r in conn.execute("PRAGMA table_info(set_daten)")}
    assert {"erschienen", "eol", "eol_geschaetzt", "uvp_eur", "bilder"} <= spalten


def test_oberflaeche_kennt_eol_und_laeuft_aus():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert '"läuft aus {datum}" : "EOL {datum}"' in js
    en = json.loads((FRONTEND / "i18n" / "en.json").read_text(encoding="utf-8"))
    assert en["EOL {datum}"] and en["läuft aus {datum}"] and en["UVP"]


def test_eckdaten_leise_und_preise_auf_dem_preisblatt():
    """Anlass: „die PreiseUVP und brickmerge, würde ich in den
    entsprechenden reiter mit rein machen. außerdem sind die eckdaten
    irgendwie ganz schön zu präsent“ (10.10.2026). Keine Kacheln mehr
    für die Eckdaten; UVP und Brickmerge stehen auf dem Blatt „Preise“, das
    Sets jetzt immer haben – auch ohne BrickLink-Zugang."""
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    html = js[js.index("function setdatenHtml("):js.index("/* UVP und Brickmerge-Preis")]
    assert "sb-kachel" not in html and "sb-eckdaten" in html
    assert "UVP" not in html and "Brickmerge" not in html
    assert "data-set-preise" in js
    assert "hatPreise || neupreis" in js


def test_setbilder_lassen_sich_in_der_grossansicht_durchwischen():
    """Anlass: „ich kann die Bilder nicht durchswipen sonder muss sie immer
    zu machen, das bild anwählen was ich sehen möchte und groß machen“
    (10.10.2026). Ein Tipp auf ein Bild der Reihe – oder auf das große
    – öffnet die Galerie mit allen Setbildern."""
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    teil = js[js.index("function setbilderEinsetzen("):]
    teil = teil[:teil.index("\n}\n")]
    assert "openGallery(" in teil and "gross.dataset.bilder" in teil
    assert "function openGallery(startUrl, gid, gtype, dazu = [])" in js
    assert "img.dataset.bilder" in js


def test_junge_sets_werden_bei_bricklink_nachgeprueft(client, monkeypatch):
    """Anlass: 75455-1 wurde zum Erscheinen gekauft und erfasst, als
    BrickLink Boba Fett noch nicht führte; der leere Inhalt stand zwei
    Monate (10.10.2026). Junge Sets werden jetzt alle zwei Wochen
    nachgesehen; alte und frisch geprüfte nicht, die Sammlung bleibt."""
    jahr = time.localtime().tm_year
    alt = int(time.time()) - 60 * 86400
    with core.db() as conn:
        for nr, j, geprueft in (("75455-1", jahr, alt), ("7190-1", 2000, alt),
                                ("75999-1", jahr, int(time.time()))):
            conn.execute("INSERT INTO collection (item_id, item_type, name,"
                         " year, added_at) VALUES (?, 'set', ?, ?, ?)",
                         (nr, nr, j, alt))
            conn.execute("INSERT INTO set_meta (set_no, figs_fetched_at)"
                         " VALUES (?, ?)", (nr, geprueft))
        vorher = conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
    gefragt = []
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    monkeypatch.setattr(integrations, "bricklink_subsets", lambda nr: (
        gefragt.append(nr) or [{"item_id": "sw1538", "qty": 1, "name": "Boba"}]))
    assert main._junge_sets_nachpruefen() == 1
    assert gefragt == ["75455-1"]
    assert _gespeichert("75455-1") == [("sw1538", 1)]
    with core.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0] == vorher
    # Gleich danach: frisch geprüft, also nichts mehr fällig
    assert main._junge_sets_nachpruefen() == 0
    assert gefragt == ["75455-1"]


def test_weniger_figuren_von_bricklink_ersetzen_nichts(client, monkeypatch):
    jahr = time.localtime().tm_year
    with core.db() as conn:
        conn.execute("INSERT INTO collection (item_id, item_type, name, year,"
                     " added_at) VALUES ('75455-1', 'set', 'x', ?, 1)", (jahr,))
        conn.execute("INSERT INTO set_contents (set_no, fig_no, qty)"
                     " VALUES ('75455-1', 'sw1538', 1)")
        conn.execute("INSERT INTO set_meta VALUES ('75455-1', 1)")
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    monkeypatch.setattr(integrations, "bricklink_subsets", lambda nr: [])
    assert main._junge_sets_nachpruefen() == 0
    assert _gespeichert("75455-1") == [("sw1538", 1)]
    with core.db() as conn:
        assert conn.execute("SELECT figs_fetched_at FROM set_meta").fetchone()[0] > 1


def test_neue_figur_zu_eigenem_set_wird_nachgefragt(client, monkeypatch):
    """Anlass: Der Nachtlauf füllte den Inhalt von 75455-1 still nach. „so
    still bitte nicht, wenn man ein set schon in der Sammlung hat und eine
    Figur dazu kommt, sollte die App fragen“ (10.10.2026). Die neue
    Figur steht danach zur Nachfrage bereit – einmal, nicht bei jedem Start."""
    jahr = time.localtime().tm_year
    with core.db() as conn:
        conn.execute("INSERT INTO collection (item_id, item_type, name, year,"
                     " condition, added_at) VALUES ('75455-1', 'set',"
                     " 'Boba Fetts Starship', ?, 'new', 1)", (jahr,))
        conn.execute("INSERT INTO set_contents (set_no, fig_no, qty)"
                     " VALUES ('75455-1', 'sw0001', 1)")
        conn.execute("INSERT INTO set_meta VALUES ('75455-1', 1)")
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: True)
    monkeypatch.setattr(integrations, "bricklink_subsets", lambda nr: [
        {"item_id": "sw0001", "qty": 1, "name": "Alt"},
        {"item_id": "sw1538", "qty": 1, "name": "Boba Fett"}])
    assert main._junge_sets_nachpruefen() == 1
    sets = client.get("/api/set_neue_figuren").json()["sets"]
    assert len(sets) == 1
    s = sets[0]
    assert (s["item_id"], s["name"], s["condition"]) == (
        "75455-1", "Boba Fetts Starship", "new")
    assert [(f["item_id"], f["qty"], f["name"]) for f in s["figuren"]] == [
        ("sw1538", 1, "Boba Fett")]
    # Sammlung bleibt, bis jemand Ja sagt
    with core.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection WHERE"
                            " item_type = 'minifig'").fetchone()[0] == 0
    assert client.post("/api/set_neue_figuren/erledigt",
                       json={"set_no": "75455-1"}).json()["ok"]
    assert client.get("/api/set_neue_figuren").json()["sets"] == []


def test_nachgefuellter_leerer_inhalt_wird_nachgefragt(client, monkeypatch):
    """Ohne BrickLink-Zugang kam beim Erfassen keine Frage, weil der Inhalt
    leer war; füllt ihn später die Setdatei, muss die Frage nachgeholt
    werden. Gelöschte Sets fragen nicht."""
    _teiledatei([("sw1538", "fig-014801")])
    with core.db() as conn:
        conn.execute("INSERT INTO collection (item_id, item_type, name,"
                     " added_at) VALUES ('75455-1', 'set', 'x', 1)")
        conn.execute("INSERT INTO set_meta (set_no, figs_fetched_at)"
                     " VALUES ('75455-1', 1)")
    _datei(monkeypatch, FIG_DATEN)
    main._setdaten_ziehen()
    sets = client.get("/api/set_neue_figuren").json()["sets"]
    assert [f["item_id"] for s in sets for f in s["figuren"]] == ["sw1538"]
    with core.db() as conn:
        conn.execute("DELETE FROM collection")
    assert client.get("/api/set_neue_figuren").json()["sets"] == []


def test_nie_geholter_inhalt_fragt_nicht(client, monkeypatch):
    """Frische Installation mit übernommener Sammlung (Windows, 23.11.2026):
    Kein Set hatte je einen Inhalt, und die App fragte beim ersten Start
    jedes Set einzeln nach. Nie geholt ist nicht „Figur kam dazu“."""
    _teiledatei([("sw1538", "fig-014801")])
    with core.db() as conn:
        conn.execute("INSERT INTO collection (item_id, item_type, name,"
                     " added_at) VALUES ('75455-1', 'set', 'x', 1)")
    _datei(monkeypatch, FIG_DATEN)
    main._setdaten_ziehen()
    assert _gespeichert("75455-1") == [("sw1538", 1)]
    assert client.get("/api/set_neue_figuren").json()["sets"] == []


def test_oberflaeche_fragt_neue_set_figuren_beim_start():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "neueSetFigurenFragen();" in js
    assert '"/set_neue_figuren"' in js
    assert '"/set_neue_figuren/erledigt"' in js


EXKLUSIV_DATEN = [
    {"nr": "75192-1", "name": "Falke", "teile": 7541,
     "figuren": [["fig-000001", 1], ["fig-000002", 1], ["fig-000003", 1]]},
    # Neuauflage desselben Sets: kein zweites Set
    {"nr": "75192-2", "name": "Falke", "teile": 7541,
     "figuren": [["fig-000001", 1]]},
    {"nr": "75300-1", "name": "Anderes", "teile": 300,
     "figuren": [["fig-000002", 1]]},
]


def test_exklusive_figuren_werden_markiert(client, monkeypatch):
    """Anlass: Sven wollte bei „Enthaltene Figuren“ sehen, welche Figur es
    nur in diesem Set gibt (10.10.2026). Wo die Teiledatei eine eindeutige
    BrickLink-Nummer kennt, wird nach ihr gezählt, sonst nach der
    Rebrickable-Nummer. Eine Neuauflage (-2) ist kein zweites Set."""
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _teiledatei([("sw0001", "fig-000001"), ("sw0002", "fig-000002"),
                 # zwei BrickLink-Figuren, ein Gegenstück: nicht eindeutig
                 ("sw0003a", "fig-000003"), ("sw0003b", "fig-000003")])
    _datei(monkeypatch, EXKLUSIV_DATEN)
    main._setdaten_ziehen()
    d = client.get("/api/set_figs/75192-1").json()
    assert {f["item_id"]: bool(f.get("exklusiv")) for f in d["items"]} == {
        "sw0001": True, "sw0002": False,
        # nicht eindeutig abgebildet → Rebrickable-Nummern, je einmal
        "fig-000003": True}


def test_exklusiv_auch_bei_gespeichertem_inhalt(client, monkeypatch):
    """Der Inhalt kommt oft von BrickLink oder aus `set_contents`, nicht aus
    der Setdatei – die Markierung muss trotzdem stehen. Eine Figur, die die
    Setdatei gar nicht kennt, ist unbekannt, nicht exklusiv."""
    monkeypatch.setattr(integrations, "bricklink_enabled", lambda: False)
    _teiledatei([("sw0001", "fig-000001"), ("sw0002", "fig-000002")])
    _datei(monkeypatch, EXKLUSIV_DATEN)
    main._setdaten_ziehen()
    with core.db() as conn:
        conn.execute("DELETE FROM set_contents")
        for no in ("sw0001", "sw0002", "sw9999"):
            conn.execute("INSERT INTO set_contents (set_no, fig_no, qty)"
                         " VALUES ('75192-1', ?, 1)", (no,))
        conn.execute("INSERT OR REPLACE INTO set_meta VALUES ('75192-1', 1)")
    d = client.get("/api/set_figs/75192-1").json()
    assert d["quelle"] == "lokal"
    assert [f["item_id"] for f in d["items"] if f.get("exklusiv")] == ["sw0001"]


def test_oberflaeche_zeigt_exklusiv():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "f.exklusiv ?" in js and 'tr("Exklusiv")' in js


def test_neues_thema_nach_dem_abzug_sofort_in_der_themenliste(client, monkeypatch):
    """Der Themenspeicher aus 3.8.2 merkt sich Themen je Prozess. Bringt
    ein Setdaten-Abzug ein neues `rb:`-Thema, muss es ohne Neustart in der
    Themenliste stehen (3.9.7)."""
    _datei(monkeypatch, DATEN)
    main._setdaten_ziehen()
    vorher = client.get("/api/katalog/liste/themen", params={"art": "set"}).json()
    assert "Ninjago" not in [t["thema"] for t in vorher["themen"]]
    _datei(monkeypatch, DATEN + [{"jahr": 2024, "name": "Ninja Dojo",
                                  "nr": "71814-1", "teile": 1394,
                                  "thema": "Ninjago"}], etag='"d2"')
    main._setdaten_ziehen()
    nachher = client.get("/api/katalog/liste/themen", params={"art": "set"}).json()
    assert "Ninjago" in [t["thema"] for t in nachher["themen"]]


def test_steckbrief_bekommt_den_deutschen_setnamen(client, monkeypatch):
    """Anlass: Die Suche fand „Supersternzerstörer“ schon über `set_namen`,
    der Steckbrief zeigte aber weiter „Executor Super Star Destroyer“
    (10.10.2026). Ohne deutschen Namen bleibt das Feld leer."""
    _datei(monkeypatch, [{"nr": "75457-1", "name": "Executor Super Star Destroyer",
                          "jahr": 2026, "teile": 1500}])
    main._setdaten_ziehen()
    with core.db() as conn:
        conn.execute("INSERT INTO set_namen (nr, sprache, name) VALUES"
                     " ('75457-1', 'de', 'Supersternzerstörer Executor')")
    assert client.get("/api/setdaten/75457").json()["name_de"] == \
        "Supersternzerstörer Executor"
    assert client.get("/api/setdaten/75192-1").json()["name_de"] == ""
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "sd.name_de" in js and "[data-setname]" in js


def test_suchtreffer_zeigt_dieselben_steckbriefdaten():
    """Anlass: „bei der suche sollten schon ein bisschen mehr daten im
    steckbrief stehen“ (10.10.2026, 10221-1 aus „Manuell erfassen“
    zeigte nur Name, Nummer und Jahr). Derselbe Baustein wie im Steckbrief,
    mit Thema, Bilderreihe und Preiszeilen."""
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    teil = js[js.index("function openSuggestModal("):]
    teil = teil[:teil.index("\n}\n")]
    assert 'setdatenEinsetzen(inner.querySelector("[data-setdaten]"), pit.item_id, true)' in teil
    assert "data-set-preise" in teil and "data-setbild" in teil and "data-setname" in teil


def test_setbilder_kommen_aus_der_setdatei(client, monkeypatch):
    """Anlass: „wobei ich gedacht hätte, die setdaten ziehen wir uns selber
    und diese werden im katalog hinterlegt“ (10.10.2026). Bis 3.9.11
    holte der Server die Bilder beim Öffnen von Brickmerge. Die Datei nennt
    nur Dateinamen; was nach einem Pfad aussieht, darf die Adresse nicht
    woandershin biegen."""
    _datei(monkeypatch, [dict(DATEN[2], bilder=[
        "4366533_238CAB5A16BEF6C4_jpeg.jpg", "LEGO_75192_alt1.jpg",
        "../../boese.jpg", "https://irgendwo.example/x.jpg", 7])])
    main._setdaten_ziehen()
    assert client.get("/api/setdaten/75192-1").json()["bilder"] == [
        "https://www.brickmerge.de/img/sets/l/4366533_238CAB5A16BEF6C4_jpeg.jpg",
        "https://www.brickmerge.de/img/sets/l/LEGO_75192_alt1.jpg"]
