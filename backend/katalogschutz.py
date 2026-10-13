"""Den verschlüsselten Figurenkatalog lesen (seit 3.8.2).

**Warum er verschlüsselt ist.** Bis dahin stand der Abzug als Klartext im
öffentlichen Repo, und das Repo steht unter MIT – jeder durfte die
Beschreibungen damit ausdrücklich nehmen. Sie sind aber unsere eigene
Arbeit und sollen nur in Nupplo genutzt werden (13.10.2026).

**Was das kann und was nicht.** Der Schlüssel steht hier im Quelltext, und
der ist öffentlich. Wer es darauf anlegt, entschlüsselt die Datei. Das ist
gewollt: Jede Installation soll den Katalog ohne Anmeldung, ohne Hub und
ohne Überwachung bekommen. Die Verschlüsselung ist ein Schild „nur für
Nupplo“, keine Festung – wer sie aufmacht, weiß, dass er etwas nimmt, das
nicht für ihn bestimmt ist (`katalog/LIZENZ.md`).

**Fallenzeilen.** Ein paar erfundene Figuren, die echt aussehen. Hier
werden sie übergangen; eine Kopie, die um Nupplo herum entschlüsselt wurde,
trägt sie mit und verrät damit, woher sie stammt.

Das Format beschreibt `docs/KATALOG-FORMAT.md`; geschrieben wird die Datei
vom Katalogdienst (`katalogschutz.py` dort). Beide rechnen gegen dieselben
Prüfwerte (`tests/katalog-pruefwerte.json`).
"""
import base64
import hashlib
import hmac

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Derselbe wie im Katalogdienst und in der App. Wechselt er, muss jede
# Fassung den neuen kennen, bevor der Dienst damit schreibt – sonst findet
# keine Installation mehr eine einzige Figur.
SCHLUESSEL = base64.b64decode("OMehifzf6ELHp8Sgt7RoVsH2sl/q5w8GPj2lMhELKW0=")
ZUSATZ = b"nupplo-katalog-1"


def _ableiten(zweck: bytes) -> bytes:
    return hmac.new(SCHLUESSEL, b"nupplo-katalog/" + zweck,
                    hashlib.sha256).digest()


_AES = AESGCM(_ableiten(b"aes"))
_NONCE = _ableiten(b"nonce")


def zeile_lesen(zeile: str):
    """Der JSON-Text einer Zeile – oder None, wenn sie übergangen wird.

    Übergangen werden Hinweise (`#`), Fallen und alles, was sich nicht mit
    unserem Schlüssel öffnen lässt. **Klartext-Zeilen (`{`) gehen weiter
    durch:** Wer `katalog_quelle` auf den eigenen Katalogdienst im Heimnetz
    stellt, bekommt dort Klartext, und das soll weiter funktionieren.
    """
    zeile = zeile.strip()
    if not zeile or zeile.startswith("#"):
        return None
    if zeile.startswith("{"):
        return zeile
    try:
        roh = base64.b64decode(zeile, validate=True)
        text = _AES.decrypt(roh[:12], roh[12:], ZUSATZ)
    except Exception:
        return None
    # Eine Falle trägt einen Nonce aus `falle\0` statt `zeile\0`. Nach
    # außen sieht sie aus wie jede andere Figur – nur so taugt sie als
    # Beleg, wenn ihr Text anderswo auftaucht.
    if roh[:12] != hmac.new(_NONCE, b"zeile\0" + text,
                            hashlib.sha256).digest()[:12]:
        return None
    return text.decode("utf-8")
