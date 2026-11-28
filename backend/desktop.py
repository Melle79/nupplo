"""Desktop-Modus: Nupplo SE als Programm auf einem einzelnen Rechner.

Für Nupplo für Windows (und jeden anderen Rechner ohne NAS): Dieselbe SE
läuft im Programm selbst, die Oberfläche ist die Web-App in einem Fenster.
Eingeschaltet nur mit `NUPPLO_DESKTOP=1` – auf dem NAS ändert sich nichts.

**Warum kein Passwort.** Am eigenen Rechner gibt es genau einen Benutzer,
und das Fenster gehört dem Programm. Ein Anmeldebildschirm vor der eigenen
Sammlung auf dem eigenen Rechner wäre nur lästig. Das Konto wird deshalb
beim ersten Start angelegt, mit einem Zufallspasswort, das niemand kennt.

**Wie das Fenster angemeldet wird.** Das Programm startet die SE mit einem
Einmal-Geheimnis (`NUPPLO_DESKTOP_GEHEIMNIS`, je Start neu) und öffnet
`/desktop/start?code=…`. Nur wer das Geheimnis kennt, bekommt ein Token –
ein anderes Programm auf demselben Rechner, das den Port findet, nicht.
Die Seite legt das Token dort ab, wo die Web-App es sucht (`bf_token`), und
springt zur Startseite. An der Web-App selbst ändert sich dafür nichts.

Die SE lauscht dabei nur auf 127.0.0.1 – das stellt das Programm sicher,
das sie startet, und `desktop_start` prüft es zusätzlich.
"""
import hmac
import json
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import core

router = APIRouter()


def an() -> bool:
    return os.environ.get("NUPPLO_DESKTOP") == "1"


def _benutzername() -> str:
    # Der Name des Windows-Kontos taugt als Anzeige („Guten Morgen, …“) –
    # er verlässt den Rechner nie. Ohne einen bleibt es neutral.
    name = (os.environ.get("NUPPLO_DESKTOP_NAME") or "").strip()[:40]
    return name if len(name) >= 2 else "Sammler"


def konto(conn=None) -> tuple[int, str]:
    """Das eine Konto dieses Rechners – beim ersten Start angelegt, als
    Eigner mit allen Rechten wie nach der Einrichtung auf dem NAS.

    `conn`: in einer schon offenen Verbindung. Eine zweite, die schreiben
    will, wartete sonst auf die erste – „database is locked“ (Durchsicht
    17.10.2026, im Abgleich)."""
    if conn is None:
        with core.db() as eigene:
            return konto(eigene)
    z = conn.execute("SELECT id, username FROM users WHERE is_admin = 1 "
                     "ORDER BY id LIMIT 1").fetchone()
    if z:
        return z["id"], z["username"]
    name = _benutzername()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, is_admin, is_dealer, created_at) "
        "VALUES (?, ?, 1, 1, ?)",
        (name, core.hash_password(secrets.token_urlsafe(32)), int(time.time())))
    return cur.lastrowid, name


def einrichten():
    if an():
        konto()


@router.get("/desktop/start")
def desktop_start(code: str, request: Request, abgleich: str = ""):
    geheimnis = os.environ.get("NUPPLO_DESKTOP_GEHEIMNIS", "")
    if not an() or not geheimnis:
        raise HTTPException(404, "Nicht gefunden")
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Nur auf diesem Rechner")
    if not hmac.compare_digest(code.encode(), geheimnis.encode()):
        raise HTTPException(403, "Falscher Code")
    uid, name = konto()
    token = core.create_token(uid, name, True)
    # Beim allerersten Fenster der Einrichtungsassistent (Name, Gebiet,
    # Preise, Tauschbörse) – auf dem NAS kommt er nach dem Anlegen des
    # Kontos, das es hier nicht gibt. Gemerkt in der Datenbank, nicht im
    # Fenster: WebView2 kann seinen Speicher verlieren, die Sammlung nicht.
    ziel = "/"
    if core.get_setting("desktop_willkommen") != "1":
        core.set_setting("desktop_willkommen", "1")
        ziel = "/?willkommen=1"
    # Beim ersten Start einen Abgleich gewählt (SE oder Cloud-Ordner):
    # gleich zu seiner Einrichtung.
    if abgleich in ("se", "wolke"):
        ziel += ("&" if "?" in ziel else "?") + "abgleich=" + abgleich
    # json.dumps als JavaScript-Zeichenkette: kein Ausbrechen aus dem Skript,
    # auch wenn der Kontoname Anführungszeichen enthält.
    # Eigene CSP mit Nonce: Die allgemeine (`script-src 'self'`) verbietet
    # Inline-Skripte, und beim ersten Probelauf am 16.10.2026 blieb das
    # Fenster deshalb weiß auf dieser Seite stehen, ohne angemeldet zu sein.
    nonce = secrets.token_urlsafe(16)
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><title>Nupplo</title>"
        "<script nonce='" + nonce + "'>"
        "localStorage.setItem('bf_token', " + json.dumps(token) + ");"
        # bf_user ist JSON, keine bloße Zeichenkette – die Web-App liest es
        # mit JSON.parse. Den Rest (Profi-Rolle, Sprache) holt `refreshMe`.
        "localStorage.setItem('bf_user', "
        + json.dumps(json.dumps({"username": name, "is_admin": True})) + ");"
        "location.replace(" + json.dumps(ziel) + ");</script>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                 "Content-Security-Policy":
                     f"default-src 'none'; script-src 'nonce-{nonce}'"})


@router.get("/api/desktop")
def desktop_stand():
    """Öffentlich: Läuft die SE als Programm? Die Web-App kann damit
    Dinge ausblenden, die am eigenen Rechner nichts bedeuten."""
    return {"desktop": an()}
