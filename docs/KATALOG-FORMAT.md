# Der Figurenkatalog `katalog/index.nupplo`

Geschrieben vom Katalogdienst, gelesen von Nupplo SE (`backend/katalogschutz.py`)
und vom externen Zugriff. Wer eine Seite ändert, ändert alle – die Prüfwerte in
`tests/daten/katalog-pruefwerte.json` gelten überall.

Die Nutzung regelt [`katalog/LIZENZ.md`](../katalog/LIZENZ.md): nur in Nupplo.
Die Verschlüsselung ist kein Geheimnis, der Schlüssel steht im Quelltext. Sie
sagt jedem, der sie umgeht, dass der Inhalt nicht für ihn bestimmt ist.

## Aufbau

Eine Textdatei, eine Zeile je Eintrag, nach Nummer sortiert.

- Zeilen, die mit `#` beginnen, sind Hinweise im Klartext → übergehen.
- Jede andere Zeile ist Base64 (Standard, mit `=`) von

      nonce (12 Byte) ‖ Geheimtext ‖ Tag (16 Byte)

  mit **AES-256-GCM**, Zusatzdaten (AAD) `nupplo-katalog-1`.
- Der Klartext ist die JSON-Zeile wie früher in `index.ndjson`:
  `item_no`, `art`, `farben`, `merkmale`, `modell`.
- Eine Zeile ohne `item_no` (die erste: `hinweis`/`notice`) → übergehen.

## Schlüssel

    K       = Base64 „OMehifzf6ELHp8Sgt7RoVsH2sl/q5w8GPj2lMhELKW0="
    K_aes   = HMAC-SHA256(K, "nupplo-katalog/aes")
    K_nonce = HMAC-SHA256(K, "nupplo-katalog/nonce")

## Fallenzeilen

    nonce einer echten Zeile = HMAC-SHA256(K_nonce, "zeile" ‖ 0x00 ‖ Klartext)[0..12]
    nonce einer Falle        = HMAC-SHA256(K_nonce, "falle" ‖ 0x00 ‖ Klartext)[0..12]

Nach dem Entschlüsseln den Nonce nachrechnen. Stimmt er nicht mit der
„zeile“-Fassung überein, ist es eine Falle → **übergehen**, nie in den Katalog
übernehmen. Fallen sind erfundene Figuren; eine Kopie, die sie trägt, stammt aus
dieser Datei.

Der Nonce hängt am Text, damit eine unveränderte Figur Nacht für Nacht dieselbe
Zeile bleibt – sonst wäre jeder Commit die ganze Datei.

## Hinweis an der alten Stelle

`katalog/index.ndjson` enthält keine Beschreibungen mehr. Ältere Fassungen
lesen dort:

- jede bekannte Nummer mit `merkmale` „bitte nupplo aktualisieren; please
  update nupplo“ – ihre Suche nach Aussehen findet danach nur noch
- zwei Hinweiszeilen, deren `item_no` und `name` der Satz ist („Bitte
  aktualisiere Nupplo: …“, „Please update Nupplo: …“).

Neue Fassungen lesen `index.nupplo` und **löschen Einträge, deren Nummer ein
Leerzeichen enthält** – das sind diese Hinweise. Danach kommt jede Beschreibung
aus der neuen Datei zurück.
