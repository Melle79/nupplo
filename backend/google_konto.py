"""Anmeldung bei Google für den Abgleich über Google Drive (Windows).

Ablauf wie für Desktop-Programme vorgesehen: Der Browser des PCs öffnet
Googles Anmeldeseite, Google schickt den Benutzer zurück an
`http://127.0.0.1:<port>/api/ordner/google/zurueck` – das ist die Nupplo
SE im Programm selbst –, und dort wird der Code gegen einen Zugang
getauscht (OAuth 2.0 mit PKCE, Berechtigung nur `drive.file`).

**Warum nicht im Fenster der App.** Google lässt die Anmeldung in
eingebetteten Browsern (WebView2) nicht zu. Darum geht sie im normalen
Browser auf, und die App fragt nach, bis der Zugang da ist.

**Was gespeichert wird.** Nur der Erneuerungs-Token, in der Datenbank auf
diesem PC, und er steht in `GEHEIME_SETTINGS`. Kein Nupplo-Server sieht ihn
und keiner sieht die Pakete – das war der Grund für den Weg über Drive
(25.11.2026: keine Sammlungen auf unseren Servern).

**Kennung des Programms.** Client-ID und -Geheimnis kommen aus dem
Google-Cloud-Projekt „nupplo“ (Typ „Desktop-App“). Bei Desktop-Programmen
gilt das Geheimnis nach Google selbst nicht als geheim, es steckt in jeder
Installation. Es steht trotzdem nicht im Quelltext, sondern kommt über
`NUPPLO_GOOGLE_CLIENT_ID` / `NUPPLO_GOOGLE_CLIENT_SECRET` oder die Datei
`google-client.json` neben dem Programm, damit es sich ohne neuen Quelltext
tauschen lässt.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import core
from ablage import AblageFehler

ANMELDEN = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
WIDERRUFEN = "https://oauth2.googleapis.com/revoke"
BERECHTIGUNG = "https://www.googleapis.com/auth/drive.file"

# Laufende Anmeldungen: state → (code_verifier, redirect_uri, Zeit).
# Nur im Speicher: Wer die App während der Anmeldung schließt, fängt neu an.
_offen: dict[str, tuple[str, str, float]] = {}
_sperre = threading.Lock()
# Der gültige Zugang (eine Stunde) – nur im Speicher.
_zugang: dict = {}


def kennung() -> tuple[str, str]:
    cid = os.environ.get("NUPPLO_GOOGLE_CLIENT_ID", "")
    geheim = os.environ.get("NUPPLO_GOOGLE_CLIENT_SECRET", "")
    if not cid:
        orte = [Path(sys.executable).parent / "google-client.json",
                Path(__file__).resolve().parent / "google-client.json"]
        for ort in orte:
            try:
                daten = json.loads(ort.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            # Wie Google die Datei herunterladen lässt: {"installed": {…}}
            daten = daten.get("installed", daten)
            cid, geheim = daten.get("client_id", ""), daten.get("client_secret", "")
            break
    return cid, geheim


def eingerichtet() -> bool:
    return bool(kennung()[0])


def verbunden() -> bool:
    return bool(core.get_setting("google_refresh"))


def _http():
    import requests
    return requests


def anmelde_adresse(rueckweg: str) -> str:
    """Die Adresse, die der Browser öffnet."""
    cid, _ = kennung()
    if not cid:
        raise AblageFehler("Google Drive ist in dieser Fassung noch nicht eingerichtet")
    pruefer = secrets.token_urlsafe(48)
    herausforderung = base64.urlsafe_b64encode(
        hashlib.sha256(pruefer.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    with _sperre:
        jetzt = time.time()
        for k in [k for k, v in _offen.items() if jetzt - v[2] > 900]:
            del _offen[k]
        _offen[state] = (pruefer, rueckweg, jetzt)
    return ANMELDEN + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": rueckweg, "response_type": "code",
        "scope": BERECHTIGUNG, "state": state,
        "code_challenge": herausforderung, "code_challenge_method": "S256",
        # offline + consent: Nur so gibt Google einen Erneuerungs-Token
        # heraus, auch wenn man sich schon einmal angemeldet hatte.
        "access_type": "offline", "prompt": "consent"})


def zurueck(state: str, code: str) -> None:
    """Google hat den Benutzer zurückgeschickt: Code gegen Zugang tauschen."""
    with _sperre:
        offen = _offen.pop(state, None)
    if offen is None:
        raise AblageFehler("Diese Anmeldung ist abgelaufen – bitte noch einmal")
    pruefer, rueckweg, _ = offen
    cid, geheim = kennung()
    r = _http().post(TOKEN, data={
        "client_id": cid, "client_secret": geheim, "code": code, "code_verifier": pruefer,
        "grant_type": "authorization_code", "redirect_uri": rueckweg}, timeout=30)
    if r.status_code != 200:
        raise AblageFehler(f"Google hat die Anmeldung abgelehnt ({r.status_code})")
    daten = r.json()
    if BERECHTIGUNG not in daten.get("scope", "").split():
        # Google lässt den Benutzer einzelne Berechtigungen abwählen. Ohne
        # drive.file kann Nupplo nichts ablegen – das gleich sagen statt
        # beim ersten Abgleich mit einem rätselhaften 403.
        raise AblageFehler("Ohne Zugriff auf Google Drive geht der Abgleich nicht – "
                           "bitte bei der Anmeldung zulassen")
    if not daten.get("refresh_token"):
        raise AblageFehler("Google hat keinen dauerhaften Zugang gegeben – bitte noch einmal")
    core.set_setting("google_refresh", daten["refresh_token"])
    _merken(daten)


def _merken(daten: dict) -> None:
    _zugang.clear()
    _zugang.update(token=daten["access_token"],
                   bis=time.time() + int(daten.get("expires_in", 3600)) - 120)


def zugangs_token() -> str:
    if _zugang and _zugang["bis"] > time.time():
        return _zugang["token"]
    refresh = core.get_setting("google_refresh")
    if not refresh:
        raise AblageFehler("Nicht bei Google angemeldet")
    cid, geheim = kennung()
    import requests
    try:
        r = requests.post(TOKEN, data={"client_id": cid, "client_secret": geheim,
                                       "refresh_token": refresh,
                                       "grant_type": "refresh_token"}, timeout=30)
    except requests.RequestException:
        raise AblageFehler("Google ist gerade nicht erreichbar")
    if r.status_code in (400, 401):
        # invalid_grant: widerrufen oder (Testmodus) nach 7 Tagen abgelaufen.
        raise AblageFehler("Die Anmeldung bei Google ist abgelaufen – bitte neu verbinden")
    if r.status_code != 200:
        raise AblageFehler(f"Google antwortet mit {r.status_code}")
    _merken(r.json())
    return _zugang["token"]


class _Sitzung:
    """Wie `requests.Session.request`, mit frischem Zugang je Anfrage."""

    def request(self, methode, url, **kw):
        import requests
        kw.setdefault("headers", {})
        kw["headers"] = {**kw["headers"], "Authorization": f"Bearer {zugangs_token()}",
                         "User-Agent": "Nupplo"}
        r = requests.request(methode, url, **kw)
        if r.status_code == 401:
            # Zugang kurz vor Ablauf von Google schon verworfen: einmal neu.
            _zugang.clear()
            kw["headers"]["Authorization"] = f"Bearer {zugangs_token()}"
            r = requests.request(methode, url, **kw)
        return r


def sitzung():
    if not verbunden():
        raise AblageFehler("Erst bei Google anmelden")
    return _Sitzung()


def abmelden() -> None:
    """Zugang bei Google zurückziehen und hier vergessen. Die Dateien in
    Drive bleiben – sie gehören dem Benutzer."""
    refresh = core.get_setting("google_refresh")
    core.set_setting("google_refresh", "")
    _zugang.clear()
    if refresh:
        try:
            _http().post(WIDERRUFEN, data={"token": refresh}, timeout=10)
        except Exception:
            pass
