"""Wo der Abgleichsordner liegt: ein Ordner auf der Platte oder Google Drive.

Das Format (`docs/ORDNER-ABGLEICH.md`) bleibt in beiden Fällen dasselbe.
`ordner.py` spricht nur mit einer `Ablage` und weiß nicht, wo die Dateien
liegen.

**Warum Google Drive über die Schnittstelle und nicht über den Drive-Ordner
auf dem PC.** Sven hat am 25.11.2026 entschieden, dass Geräte ohne SE über
Google Drive abgleichen (wer ein Android-Telefon hat, hat Google Drive),
ohne dass Nupplo selbst etwas speichert. Android hat keinen Drive-Ordner
auf der Platte, Telefone sprechen also mit der Drive-Schnittstelle. Nupplo
bekommt dort nur `drive.file`: Es sieht nur Dateien, die es selbst angelegt
hat, nicht den Rest des Drives – und Google stuft das als unbedenklich
ein, ohne Sicherheitsprüfung. Legte Windows die Pakete über das Programm
„Google Drive für den Desktop“ ab, hätte sie nicht Nupplo angelegt, und
die Telefone sähen sie nie. Darum geht auch Windows über die Schnittstelle.

**Was Drive anders macht als eine Platte.** Namen sind nicht eindeutig: Zwei
Dateien „ordner.json“ im selben Ordner sind erlaubt, und „nur anlegen, wenn
es das noch nicht gibt“ gibt es nicht. Wer zuerst angelegt hat, entscheidet
deshalb die Reihenfolge (`createdTime`, dann `id`), die jedes Gerät gleich
sieht. Dafür taucht eine hochgeladene Datei erst auf, wenn sie ganz da ist –
das Umbenennen nach dem Schreiben braucht es dort nicht.
"""
import json
import os
import re
import secrets
from pathlib import Path

UNTERORDNER = "Nupplo-Abgleich"
PAKET_NAME = re.compile(r"^(\d{8})\.paket$")
GERAET_NAME = re.compile(r"^[0-9a-f]{16}$")


class AblageFehler(Exception):
    """Die Ablage ist gerade nicht erreichbar (kein Netz, abgemeldet …)."""


class Ablage:
    """Was `ordner.py` vom Abgleichsordner braucht – nicht mehr."""

    def da(self) -> bool:
        """Ist der Ordner gerade erreichbar?"""
        raise NotImplementedError

    def kopf(self) -> bytes | None:
        """Inhalt von ordner.json, None wenn es keinen gibt."""
        raise NotImplementedError

    def kopf_anlegen(self, inhalt: bytes) -> bool:
        """ordner.json anlegen. False, wenn schon einer da war – dann gilt
        der, und der Aufrufer tritt bei statt zu überschreiben."""
        raise NotImplementedError

    def geraete(self) -> list[str]:
        raise NotImplementedError

    def geraet_anlegen(self, geraet: str) -> None:
        raise NotImplementedError

    def pakete(self, geraet: str) -> list[int]:
        raise NotImplementedError

    def paket(self, geraet: str, nummer: int) -> bytes:
        raise NotImplementedError

    def paket_schreiben(self, geraet: str, nummer: int, daten: bytes) -> None:
        raise NotImplementedError

    def beschreibung(self) -> str:
        """Für die Anzeige: wo der Abgleich liegt."""
        raise NotImplementedError


# ------------------------------------------------------------ Platte

class Platte(Ablage):
    """Ein Ordner, den ein Cloud-Programm auf dem PC abgleicht – OneDrive,
    Dropbox, iCloud für Windows … Wie in Windows 0.4.0."""

    def __init__(self, pfad: str):
        p = Path(pfad).expanduser()
        self.gewaehlt = p
        # Der gewählte Ordner selbst, wenn er schon der Abgleichsordner ist,
        # sonst `Nupplo-Abgleich` darin – so findet jedes Gerät dieselbe
        # Stelle, egal ob man den Cloud-Ordner oder den Unterordner auswählt.
        self.w = p if (p / "ordner.json").is_file() else p / UNTERORDNER

    def da(self) -> bool:
        return self.w.is_dir()

    def kopf(self) -> bytes | None:
        try:
            return (self.w / "ordner.json").read_bytes()
        except FileNotFoundError:
            return None

    def kopf_anlegen(self, inhalt: bytes) -> bool:
        self.w.mkdir(parents=True, exist_ok=True)
        (self.w / "geraete").mkdir(exist_ok=True)
        # „x“: Legen zwei Geräte gleichzeitig an, gewinnt eines, und das
        # andere erfährt es, statt den Schlüssel des ersten zu überschreiben.
        try:
            with open(self.w / "ordner.json", "xb") as f:
                f.write(inhalt)
        except FileExistsError:
            return False
        return True

    def geraete(self) -> list[str]:
        try:
            return sorted(d.name for d in (self.w / "geraete").iterdir()
                          if d.is_dir() and GERAET_NAME.match(d.name))
        except FileNotFoundError:
            return []

    def geraet_anlegen(self, geraet: str) -> None:
        (self.w / "geraete" / geraet).mkdir(parents=True, exist_ok=True)

    def pakete(self, geraet: str) -> list[int]:
        d = self.w / "geraete" / geraet
        if not d.is_dir():
            return []
        return sorted(int(m.group(1)) for f in d.iterdir() if (m := PAKET_NAME.match(f.name)))

    def paket(self, geraet: str, nummer: int) -> bytes:
        return (self.w / "geraete" / geraet / f"{nummer:08d}.paket").read_bytes()

    def paket_schreiben(self, geraet: str, nummer: int, daten: bytes) -> None:
        eigen = self.w / "geraete" / geraet
        eigen.mkdir(parents=True, exist_ok=True)
        # Erst unter anderem Namen, dann umbenennen: Ein halb geschriebenes
        # Paket soll der Cloud-Dienst nie unter dem richtigen Namen sehen.
        zwischen = eigen / f".{nummer:08d}.{secrets.token_hex(4)}.neu"
        zwischen.write_bytes(daten)
        os.replace(zwischen, eigen / f"{nummer:08d}.paket")

    def beschreibung(self) -> str:
        return str(self.gewaehlt)


# ------------------------------------------------------------ Google Drive

ORDNER_TYP = "application/vnd.google-apps.folder"
API = "https://www.googleapis.com/drive/v3"
HOCHLADEN = "https://www.googleapis.com/upload/drive/v3"


class GoogleDrive(Ablage):
    """Der Abgleichsordner in Google Drive, angesprochen über die
    Schnittstelle (v3). `http` ist eine `requests.Session` mit gültiger
    Anmeldung (`google_konto.sitzung()`); im Test ein nachgebautes Drive.

    Aufbau wie auf der Platte: `Nupplo-Abgleich/ordner.json`,
    `Nupplo-Abgleich/geraete/<geraet>/00000001.paket`. Der Ordner liegt
    oben in „Meine Ablage“, damit man ihn in Drive wiederfindet.
    """

    def __init__(self, http):
        self.http = http
        self._ids: dict[tuple, str] = {}

    # -- Drive-Grundlagen

    def _anfrage(self, methode, url, **kw):
        import requests
        try:
            r = self.http.request(methode, url, timeout=30, **kw)
        except requests.RequestException as e:
            raise AblageFehler(f"Google Drive ist gerade nicht erreichbar ({type(e).__name__})")
        if r.status_code == 401:
            raise AblageFehler("Die Anmeldung bei Google ist abgelaufen – bitte neu verbinden")
        if r.status_code >= 400:
            raise AblageFehler(f"Google Drive antwortet mit {r.status_code}")
        return r

    def _liste(self, q: str) -> list[dict]:
        """Alle Treffer, älteste zuerst. Bei gleicher Zeit entscheidet die
        id – so wählt jedes Gerät denselben, wenn es einen Namen doppelt gibt."""
        dateien, seite = [], None
        while True:
            params = {"q": q + " and trashed = false", "spaces": "drive", "pageSize": 1000,
                      "fields": "nextPageToken, files(id, name, mimeType, createdTime)"}
            if seite:
                params["pageToken"] = seite
            antwort = self._anfrage("GET", f"{API}/files", params=params).json()
            dateien += antwort.get("files", [])
            seite = antwort.get("nextPageToken")
            if not seite:
                break
        return sorted(dateien, key=lambda f: (f.get("createdTime", ""), f["id"]))

    @staticmethod
    def _q(name: str) -> str:
        return name.replace("\\", "\\\\").replace("'", "\\'")

    def _kinder(self, eltern: str, name: str | None = None, ordner: bool | None = None):
        q = f"'{eltern}' in parents"
        if name is not None:
            q += f" and name = '{self._q(name)}'"
        if ordner is True:
            q += f" and mimeType = '{ORDNER_TYP}'"
        elif ordner is False:
            q += f" and mimeType != '{ORDNER_TYP}'"
        return self._liste(q)

    def _ordner_anlegen(self, eltern: str, name: str) -> str:
        r = self._anfrage("POST", f"{API}/files", params={"fields": "id"},
                          json={"name": name, "mimeType": ORDNER_TYP, "parents": [eltern]})
        return r.json()["id"]

    def _ordner(self, eltern: str, name: str, anlegen: bool) -> str | None:
        """Unterordner nach Namen. Gibt es ihn doppelt (zwei Geräte legten
        gleichzeitig an), gilt der älteste – alle anderen sehen es genauso."""
        schluessel = (eltern, name)
        if schluessel in self._ids:
            return self._ids[schluessel]
        treffer = self._kinder(eltern, name, ordner=True)
        if not treffer:
            if not anlegen:
                return None
            self._ordner_anlegen(eltern, name)
            # Neu nachsehen statt die eigene id zu nehmen: Hat ein anderes
            # Gerät im selben Augenblick angelegt, gilt dessen, wenn er älter ist.
            treffer = self._kinder(eltern, name, ordner=True)
        self._ids[schluessel] = treffer[0]["id"]
        return treffer[0]["id"]

    def _wurzel(self, anlegen=False) -> str | None:
        return self._ordner("root", UNTERORDNER, anlegen)

    def _geraete_ordner(self, anlegen=False) -> str | None:
        w = self._wurzel(anlegen)
        return w and self._ordner(w, "geraete", anlegen)

    def _laden(self, datei_id: str) -> bytes:
        return self._anfrage("GET", f"{API}/files/{datei_id}", params={"alt": "media"}).content

    def _hochladen(self, eltern: str, name: str, daten: bytes) -> str:
        """Eine neue Datei in einem Zug (multipart). Drive zeigt sie erst,
        wenn sie ganz angekommen ist."""
        grenze = "nupplo" + secrets.token_hex(8)
        meta = json.dumps({"name": name, "parents": [eltern]}).encode()
        koerper = (f"--{grenze}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
                   + meta + f"\r\n--{grenze}\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
                   + daten + f"\r\n--{grenze}--\r\n".encode())
        r = self._anfrage("POST", f"{HOCHLADEN}/files",
                          params={"uploadType": "multipart", "fields": "id, createdTime"},
                          data=koerper,
                          headers={"Content-Type": f"multipart/related; boundary={grenze}"})
        return r.json()["id"]

    def _loeschen(self, datei_id: str) -> None:
        self._anfrage("DELETE", f"{API}/files/{datei_id}")

    # -- Ablage

    def da(self) -> bool:
        return self._wurzel() is not None

    def kopf(self) -> bytes | None:
        w = self._wurzel()
        if w is None:
            return None
        treffer = self._kinder(w, "ordner.json", ordner=False)
        return self._laden(treffer[0]["id"]) if treffer else None

    def kopf_anlegen(self, inhalt: bytes) -> bool:
        w = self._wurzel(anlegen=True)
        self._geraete_ordner(anlegen=True)
        if self._kinder(w, "ordner.json", ordner=False):
            return False
        mein = self._hochladen(w, "ordner.json", inhalt)
        # Drive kennt kein exklusives Anlegen. Haben zwei Geräte zugleich
        # angelegt, gilt das älteste ordner.json; das jüngere nimmt seines
        # wieder weg und tritt bei. Sonst hätte jedes einen anderen Schlüssel.
        erstes = self._kinder(w, "ordner.json", ordner=False)[0]["id"]
        if erstes != mein:
            self._loeschen(mein)
            return False
        return True

    def geraete(self) -> list[str]:
        g = self._geraete_ordner()
        if g is None:
            return []
        return sorted({f["name"] for f in self._kinder(g, ordner=True) if GERAET_NAME.match(f["name"])})

    def geraet_anlegen(self, geraet: str) -> None:
        self._ordner(self._geraete_ordner(anlegen=True), geraet, anlegen=True)

    def _paket_dateien(self, geraet: str) -> dict[int, list[str]]:
        g = self._geraete_ordner()
        d = g and self._ordner(g, geraet, anlegen=False)
        if not d:
            return {}
        dateien: dict[int, list[str]] = {}
        for f in self._kinder(d, ordner=False):
            m = PAKET_NAME.match(f["name"])
            if m:
                dateien.setdefault(int(m.group(1)), []).append(f["id"])
        return dateien

    def pakete(self, geraet: str) -> list[int]:
        self._zuletzt = (geraet, self._paket_dateien(geraet))
        return sorted(self._zuletzt[1])

    def paket(self, geraet: str, nummer: int) -> bytes:
        zuletzt = getattr(self, "_zuletzt", None)
        dateien = zuletzt[1] if zuletzt and zuletzt[0] == geraet else self._paket_dateien(geraet)
        ids = dateien.get(nummer)
        if not ids:
            raise FileNotFoundError(f"{geraet}/{nummer:08d}.paket")
        # Dieselbe Nummer zweimal gibt es nur, wenn ein Hochladen als
        # gescheitert galt, aber doch ankam. Beide tragen dann denselben
        # Inhalt; die älteste reicht.
        return self._laden(ids[0])

    def paket_schreiben(self, geraet: str, nummer: int, daten: bytes) -> None:
        d = self._ordner(self._geraete_ordner(anlegen=True), geraet, anlegen=True)
        self._hochladen(d, f"{nummer:08d}.paket", daten)

    def beschreibung(self) -> str:
        return f"Google Drive: Meine Ablage/{UNTERORDNER}"
