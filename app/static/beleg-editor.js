// WerkPlus Beleg-Editor - Komfort fuer den Positionseditor (Kplus-Style).
// Kern-Interaktion laeuft ueber HTMX; hier nur das Auswahl-Highlight der
// angeklickten Grid-Zeile (das Detail-Panel wird per hx-get geladen).

(function () {
  "use strict";

  function markiereZeile(row) {
    document
      .querySelectorAll("tr[data-pos-id]")
      .forEach((r) => r.classList.remove("table-active"));
    row.classList.add("table-active");
  }

  // Klick auf eine Positionszeile hebt sie sofort hervor. Aktionsbuttons rufen
  // stopPropagation() auf und erreichen diesen Handler daher nicht.
  document.addEventListener("click", function (e) {
    const row = e.target.closest && e.target.closest("tr[data-pos-id]");
    if (row) markiereZeile(row);
  });
})();
