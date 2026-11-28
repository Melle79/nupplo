# Abgleich über einen eigenen Cloud-Ordner – Format 1

Stand 06.11.2026, zuerst gebaut in Nupplo für Windows 0.4.0
(`backend/ordner.py`, Tests in `tests/test_ordner.py`). iPhone, Mac und
später Android lesen und schreiben **dasselbe** Format, damit alle Geräte
eines Haushalts denselben Ordner benutzen können. Wer hier etwas ändert,
ändert `ordner.py` mit, und umgekehrt.

## Warum so

- Geräte ohne SE verbinden sich nur über einen frei gewählten eigenen
  Cloud-Ordner (16.10.2026). Es gibt keinen Server von uns dazwischen.
- Cloud-Dienste gleichen Dateien irgendwann ab, nicht sofort, und in
  beliebiger Reihenfolge. Schreiben zwei Geräte **dieselbe** Datei, entsteht
  eine Konfliktkopie, und eine Seite geht verloren. Darum:
  **Jedes Gerät schreibt nur neue Dateien in seinen eigenen Unterordner und
  fasst sie danach nie wieder an.** Gelesen wird alles.
- Derselbe Ordner darf auch zwei Personen gehören (eine gemeinsame Sammlung
  ohne SE). Benutzer und Rollen gibt es dafür nicht.
- Der Cloud-Anbieter darf die Sammlung nicht lesen können.

## Aufbau des Ordners

```
<vom Benutzer gewählter Ordner>/
  Nupplo-Abgleich/
    ordner.json                 einmal angelegt, nie geändert
    geraete/
      <geraet>/                 16 Hex-Zeichen, zufällig je Einrichtung
        00000001.paket
        00000002.paket          lückenlos aufsteigend, nie umgeschrieben
```

Wählt jemand direkt den Ordner `Nupplo-Abgleich` (er enthält
`ordner.json`), gilt dieser.

### ordner.json

```json
{"format": 1, "id": "3f9c0a1b2c3d4e5f", "erstellt": 1793000000000,
 "pruef": "<Base64>"}
```

- `id`: 16 Hex-Zeichen, zufällig.
- `pruef`: `Base64(HMAC-SHA256(schluessel, "nupplo-ordner-pruef-1" || id))`.
  Daran erkennt ein Gerät einen falschen Code, ohne dass der Schlüssel im
  Ordner liegt.
- Angelegt mit „nur wenn noch nicht vorhanden“ (exklusives Anlegen). Wer
  verliert, tritt bei, statt zu überschreiben.

### Schlüssel und Code

- 32 zufällige Bytes, erzeugt vom ersten Gerät.
- Weitergegeben **nur von Hand** als Code: Base32 (RFC 4648, ohne `=`), in
  Vierergruppen mit `-`, 13 Gruppen. Beim Lesen: Großbuchstaben, `0`→`O`
  und `1`→`I`, dann alles außer `A–Z2–7` weglassen.
- Als QR-Code (Windows 0.4.1, `GET /api/ordner/qr`): Inhalt
  `NUPPLO-ORDNER:<Code ohne Bindestriche>:<Ordner-ID in Großbuchstaben>`.
  Nur Großbuchstaben, Ziffern und `:` – das packt der QR-Code am dichtesten.
  Ein Telefon prüft mit der ID, ob der gewählte Ordner der richtige ist.

### Paket

Datei = `"NUPPLO-PAKET-1\n"` (15 Byte) + Nonce (12 Byte) + AES-256-GCM.

- Klartext vor dem Verschlüsseln: `gzip(JSON)`, UTF-8.
- Zusätzliche Daten (AAD): `"<ordner-id>/<geraet>/<nummer>"` mit der Nummer
  als Dezimalzahl ohne führende Nullen. Ein umbenanntes oder verschobenes
  Paket lässt sich so nicht mehr öffnen.
- Geschrieben unter einem anderen Namen (`.…neu`), dann umbenannt.
- Höchstens 2.000 Sätze je Paket; mehr gehen in mehrere Pakete.

```json
{"format": 1, "geraet": "a1b2c3d4e5f60718", "nummer": 7,
 "zeit": 1793000000000,
 "saetze": [
   {"table": "collection", "uuid": "…", "updated_at": 1793000000000,
    "herkunft": "a1b2c3d4e5f60718",
    "fields": { …wie in /api/sync/pull… }},
   {"table": "collection", "uuid": "…", "updated_at": 1793000000500,
    "herkunft": "…", "deleted": true}
 ]}
```

- `fields`: genau die Felder aus `nupplo-ios/docs/SYNC-API.md`
  (Verweise als UUIDs, Benutzer als Namen).
- **Ganze Sätze, keine Änderungsschritte.**
- `updated_at`: Millisekunden, Zeit der Änderung auf dem Gerät.
- `herkunft`: das Gerät, das den Satz angelegt hat. Es bleibt bei jeder
  späteren Fassung gleich.

## Lesen

1. Je fremdem Gerät die Pakete ab der gemerkten Nummer, **lückenlos**:
   Fehlt eine Nummer oder lässt sich ein Paket nicht öffnen, hört das Lesen
   für dieses Gerät dort auf. Der nächste Lauf versucht es wieder. Cloud-
   Dienste legen Dateien an, bevor sie ganz da sind.
2. Je Satz (`table`, `uuid`) gilt die Fassung mit dem größten
   `(updated_at, geraet)`. Das ergibt auf jedem Gerät dasselbe, egal in
   welcher Reihenfolge die Pakete ankamen.
3. Anwenden: erst anlegen und ändern (Eltern vor Kindern), dann löschen
   (Kinder vor Eltern). Ist ein Satz hier vor diesem Lauf geändert und noch
   nicht geschrieben worden, gewinnt er, wenn `(updated_at, eigenes Gerät)`
   größer ist.
4. Ein Kind ohne Elternsatz wartet und wird im nächsten Lauf wieder
   versucht.
5. Was das Anwenden nebenbei ändert (Kinder einer Löschung, eine
   nachgezogene Summe), ist **Echo** und wird nicht zurückgeschrieben.
   Das andere Gerät hat es genauso getan.

### Dieselbe Figur unter zwei UUIDs

Zwei Geräte erfassen denselben Artikel, bevor sie voneinander wissen
(gleicher Schlüssel: `item_id`, `item_type`, `condition` in `collection`;
`item_id`, `item_type` in `wanted`).

- Es bleibt der Satz mit der **kleineren UUID** (Textvergleich).
- **Nur das Gerät, das den unterlegenen Satz angelegt hat** (`herkunft`),
  legt zusammen: Es addiert die Menge (`collection`, höchstens 9999),
  übernimmt die bleibende UUID mit der Zeit *jetzt*, schreibt eine
  Löschung der alten UUID und schreibt deren Kinder mit dem neuen Verweis
  neu.
- Alle anderen zeigen bis dahin nur den bleibenden Satz und blenden den
  anderen aus. Täten es alle, die das Paar bemerken, käme die Menge
  mehrfach dazu (Test
  `test_wer_das_paar_spaeter_bemerkt_zaehlt_nicht_noch_einmal_dazu`).

## Schreiben

Alles, was sich hier seit dem letzten Paket geändert hat und kein Echo
ist, als ein neues Paket (oder mehrere). Gelöschte Sätze nur, wenn der
Ordner sie schon kannte. Ohne Änderung wird **kein** Paket geschrieben.

## Beitreten mit eigener Sammlung

Liegt auf dem Gerät schon etwas, fragt es: **Zusammenlegen** (alles geht in
den Ordner, gleiche Figuren werden nach der Regel oben zusammengezählt)
oder **hier ersetzen** (die Sammlung hier wird gelöscht, ohne dass eine
Löschung in den Ordner geht, dann kommt alles aus dem Ordner).

## Google Drive

Seit 25.11.2026 (Sven: Abgleich ohne SE über Google Drive, ohne dass
Nupplo etwas speichert). Windows 0.8.0 zuerst (`backend/ablage.py`),
iPhone, Mac und Android lesen und schreiben dasselbe.

- **Über die Schnittstelle (Drive v3), nicht über einen Drive-Ordner auf
  der Platte.** Berechtigung nur `https://www.googleapis.com/auth/drive.file`:
  Nupplo sieht nur Dateien, die Nupplo angelegt hat. Was „Google Drive für
  den Desktop“ oder der Benutzer von Hand in den Ordner legt, sieht kein
  Gerät. Darum legen **alle** Geräte über die Schnittstelle an.
- **Alle Fassungen benutzen Kennungen aus demselben Google-Cloud-Projekt**
  („nupplo“; je Plattform eine Client-ID). `drive.file` gilt für das
  Projekt – das ist beim ersten echten Lauf mit zwei Plattformen zu
  bestätigen.
- Aufbau wie oben, oben in „Meine Ablage“:
  `Nupplo-Abgleich/ordner.json`, `Nupplo-Abgleich/geraete/<geraet>/00000001.paket`.
  Ordner haben den Typ `application/vnd.google-apps.folder`.
- **Namen sind in Drive nicht eindeutig.** Gibt es einen Namen doppelt
  (zwei Geräte legten gleichzeitig an), gilt der Eintrag mit der kleinsten
  `createdTime`, bei Gleichstand die kleinere `id`. Jedes Gerät sortiert
  selbst so; auf die Reihenfolge von `files.list` ist kein Verlass.
- **Einrichten:** Ordner suchen (sonst anlegen und neu suchen), dann
  `ordner.json` hochladen und danach alle `ordner.json` auflisten. Ist das
  eigene nicht das erste, das eigene löschen und als „schon ein Abgleich
  da“ behandeln. Ersetzt das exklusive Anlegen auf der Platte.
- **Pakete** einfach hochladen (`uploadType=multipart`); Drive zeigt eine
  Datei erst, wenn sie ganz da ist, das Umbenennen entfällt. Dieselbe
  Nummer zweimal (Hochladen galt als gescheitert, kam aber an): die
  älteste lesen, beide tragen dasselbe.
- **Abfragen** immer mit `trashed = false`.
- Anmeldung: OAuth 2.0 mit PKCE, `access_type=offline`. Der
  Erneuerungs-Token bleibt auf dem Gerät (Windows: Datenbank, Apple:
  Schlüsselbund, Android: verschlüsselte Einstellungen).

## Offen

- **Verdichten.** Pakete werden nie gelöscht, der Ordner wächst. Plan:
  Ein Gerät schreibt einen Stand mit allen geltenden Sätzen als Paket und
  löscht danach nur seine **eigenen** älteren Pakete. Weil jedes Paket ganze
  Sätze trägt und die neueste Fassung gilt, ersetzt der Stand alles Ältere.
  Erst nötig, wenn ein Ordner spürbar groß wird.
- Ein Gerät, das nie wiederkommt, legt seine Doppelten nie zusammen; sie
  bleiben auf den anderen ausgeblendet.
- Gerätenamen im Ordner (welches Gerät zuletzt schrieb).
