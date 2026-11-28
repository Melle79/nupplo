/* Design vor dem ersten Zeichnen setzen, damit nichts aufblitzt.

   Steht bewusst in einer eigenen Datei und nicht als `<script>` im Dokument:
   Die Sicherheits-Regeln der App erlauben nur Skript aus der App selbst
   (`script-src 'self'`). Inline blockierte der Browser es – still, und wer
   ein dunkles Design nutzte, sah bei jedem Laden kurz das helle aufblitzen.

   Muss synchron im `<head>` laufen, vor dem Stylesheet-Aufbau. Deshalb hier
   nur diese paar Zeilen und nichts sonst. */
(function () {
  try {
    var t = localStorage.getItem("bf_theme");
    var c = { galaxy: "#0C1322", nova: "#0A0E1A" }[t];
    if (t && t !== "classic") {
      document.documentElement.dataset.theme = t;
      var m = document.querySelector('meta[name="theme-color"]');
      if (m && c) m.content = c;
    }
  } catch (e) { /* privater Modus o. Ä. */ }

  /* **Weihnachten: Der Turm wird zum Baum**, vom ersten Advent bis
     Dreikönig (22.11.2026). Hier und nicht in `app.js`, damit schon
     der Startbildschirm den Baum zeigt und nicht erst den Turm.

     Der erste Advent wandert: Er ist der vierte Sonntag vor dem 25.12.,
     also zwischen 27.11. und 03.12. Ein festes Datum wäre in den meisten
     Jahren falsch.

     `?weihnachten=an` / `aus` stellt es zum Ansehen um und merkt es sich;
     `?weihnachten=auto` gibt es wieder dem Kalender. */
  try {
    var q = /[?&]weihnachten=(an|aus|auto)/.exec(location.search);
    if (q) {
      if (q[1] === "auto") localStorage.removeItem("bf_weihnachten");
      else localStorage.setItem("bf_weihnachten", q[1]);
    }
    var w = localStorage.getItem("bf_weihnachten");
    if (w === "an" || (w !== "aus" && istWeihnachtszeit(new Date()))) {
      document.documentElement.classList.add("weihnachten");
    }
  } catch (e) {
    if (istWeihnachtszeit(new Date())) document.documentElement.classList.add("weihnachten");
  }

  /* **Notausgang für den Startbildschirm.** Er ist von Haus aus sichtbar
     und deckt die ganze Seite ab; weggenommen wird er von `app.js`, wenn
     die erste Ansicht steht. Bricht die Datei ab – Syntaxfehler nach einem
     halben Update, blockiert von einer Erweiterung –, läge der Schirm für
     immer über einer App, die man nicht mehr bedienen kann.

     Lieber ohne Startbild als ausgesperrt. Sechs Sekunden sind dabei weit
     jenseits jedes normalen Starts; wer hier ankommt, hat ohnehin ein
     Problem, das ein Startbild nicht schöner macht.

     Gehört hierher und nicht als `<script>` ins Dokument: `script-src
     'self'` blockiert Inline-Skript still – die Sicherheitsprobe
     `test_kein_inline_skript` hat genau das beim Einbau gemeldet. */
  setTimeout(function () {
    var s = document.getElementById("splash");
    if (s) s.remove();
    // Die Marke hält den Inhalt unsichtbar – sie muss hier mit weg,
    // sonst stünde die App zwar da, aber durchsichtig.
    if (document.body) document.body.classList.remove("splash-laeuft");
  }, 6000);
})();

/* Erster Advent bis einschließlich 06.01. – nach der Ortszeit des Geräts,
   so wie man den Kalender an der Wand liest. */
function istWeihnachtszeit(d) {
  var j = d.getFullYear(), m = d.getMonth(), t = d.getDate();
  if (m === 0) return t <= 6;
  if (m !== 10 && m !== 11) return false;
  var heilig = new Date(j, 11, 25);
  var tag = heilig.getDay();                       // 0 = Sonntag
  var advent4 = 25 - (tag === 0 ? 7 : tag);        // Dezembertag
  var advent1 = new Date(j, 11, advent4 - 21);     // kann im November liegen
  return new Date(j, m, t) >= advent1;
}
