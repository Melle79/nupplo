"""Google Drive (v3) im Kleinen – genau so viel, wie `ablage.GoogleDrive`
benutzt, mit den Eigenheiten, auf die es ankommt:

- Namen sind nicht eindeutig; zwei Dateien gleichen Namens im selben
  Ordner sind erlaubt.
- `createdTime` steigt, `id` ist zufällig – wer zuerst anlegte, steht in
  der Liste nicht unbedingt vorn, wenn man nach id sortiert.
- `drive.file`: Eine Anmeldung sieht nur Dateien, die „Nupplo“ angelegt
  hat. Was ein anderes Programm (etwa Google Drive für den Desktop) in
  denselben Ordner legt, bleibt unsichtbar.
- Fehler auf Wunsch (`stoerung`), um einen Ausfall mitten im Lauf zu prüfen.

Echte Anfragen gehen nicht hinaus: `request()` beantwortet alles selbst.
"""
import json
import re
import secrets
import urllib.parse

ORDNER = "application/vnd.google-apps.folder"


class Antwort:
    def __init__(self, status=200, daten=None, inhalt=b""):
        self.status_code = status
        self._daten = daten
        self.content = inhalt

    def json(self):
        return self._daten


class NachgebautesDrive:
    def __init__(self):
        self.dateien: dict[str, dict] = {}
        self.uhr = 0
        # (methode, Teil der Adresse) → Statuscode, einmalig
        self.stoerung: list[tuple[str, str, int]] = []
        # Wird vor dem Hochladen aufgerufen: so legt „ein anderes Gerät“
        # im selben Augenblick etwas an.
        self.vor_hochladen = None
        self.anfragen = 0

    # -- Werkzeuge für Tests

    def anlegen(self, name, eltern, mime="application/octet-stream", inhalt=b"", app="nupplo"):
        self.uhr += 1
        fid = secrets.token_hex(6)
        self.dateien[fid] = {"id": fid, "name": name, "mimeType": mime, "parents": [eltern],
                             "createdTime": f"2026-11-25T17:{self.uhr // 60:02d}:{self.uhr % 60:02d}.000Z",
                             "inhalt": inhalt, "app": app, "trashed": False}
        return fid

    def pfad(self, *namen):
        """id des Ordners/der Datei unter einem Pfad (erster Treffer)."""
        eltern = "root"
        for n in namen:
            treffer = sorted((f for f in self.dateien.values()
                              if f["name"] == n and eltern in f["parents"]),
                             key=lambda f: f["createdTime"])
            if not treffer:
                return None
            eltern = treffer[0]["id"]
        return eltern

    def inhalte(self):
        return [f["inhalt"] for f in self.dateien.values() if f["inhalt"]]

    # -- Schnittstelle

    def request(self, methode, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.anfragen += 1
        params = params or {}
        for i, (m, teil, status) in enumerate(self.stoerung):
            if m == methode and teil in url:
                del self.stoerung[i]
                return Antwort(status, {"error": {"code": status}})
        pfad = urllib.parse.urlparse(url).path
        if methode == "GET" and pfad == "/drive/v3/files":
            return self._liste(params)
        if methode == "POST" and pfad == "/drive/v3/files":
            fid = self.anlegen(json["name"], json["parents"][0], json.get("mimeType", ""))
            return Antwort(200, {"id": fid})
        if methode == "POST" and pfad == "/upload/drive/v3/files":
            assert params.get("uploadType") == "multipart"
            meta, inhalt = self._teile(data, headers["Content-Type"])
            if self.vor_hochladen:
                haken, self.vor_hochladen = self.vor_hochladen, None
                haken(meta)
            fid = self.anlegen(meta["name"], meta["parents"][0], inhalt=inhalt)
            return Antwort(200, {"id": fid, "createdTime": self.dateien[fid]["createdTime"]})
        m = re.fullmatch(r"/drive/v3/files/([^/]+)", pfad)
        if m:
            f = self.dateien.get(m.group(1))
            if f is None or f["trashed"] or f["app"] != "nupplo":
                return Antwort(404, {"error": {"code": 404}})
            if methode == "GET" and params.get("alt") == "media":
                return Antwort(200, None, f["inhalt"])
            if methode == "DELETE":
                del self.dateien[f["id"]]
                return Antwort(204)
        return Antwort(400, {"error": f"nicht nachgebaut: {methode} {pfad}"})

    @staticmethod
    def _teile(daten: bytes, typ: str):
        grenze = typ.split("boundary=")[1].encode()
        teile = daten.split(b"--" + grenze)
        meta = teile[1].split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        inhalt = teile[2].split(b"\r\n\r\n", 1)[1][:-2]
        return json.loads(meta), inhalt

    def _liste(self, params):
        bedingungen = [b.strip() for b in params["q"].split(" and ")]
        treffer = []
        for f in self.dateien.values():
            # drive.file: nur, was Nupplo selbst angelegt hat
            if f["app"] != "nupplo":
                continue
            if all(self._passt(f, b) for b in bedingungen):
                treffer.append({k: f[k] for k in ("id", "name", "mimeType", "createdTime")})
        # Drive sortiert ohne orderBy nicht verlässlich – hier absichtlich
        # nach id, damit sich niemand auf die Reihenfolge verlässt.
        treffer.sort(key=lambda f: f["id"])
        return Antwort(200, {"files": treffer})

    @staticmethod
    def _passt(f, b):
        if m := re.fullmatch(r"'(.+)' in parents", b):
            return m.group(1) in f["parents"]
        if m := re.fullmatch(r"name = '(.*)'", b):
            return f["name"] == m.group(1).replace("\\'", "'").replace("\\\\", "\\")
        if m := re.fullmatch(r"mimeType (!?=) '(.+)'", b):
            return (f["mimeType"] == m.group(2)) == (m.group(1) == "=")
        if b == "trashed = false":
            return not f["trashed"]
        raise AssertionError(f"Abfrage nicht nachgebaut: {b}")
