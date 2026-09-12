// WerkPlus – Kplus-Belegübersicht: Zeilen markieren, Aktionsleiste speisen, Summenzeile.
//
// Bedienung wie in Kplus:
//   Klick            = Zeile markieren (nur diese)
//   Strg/Cmd + Klick = Zeile zusätzlich markieren / abwählen (Summe addiert sich)
//   Doppelklick/Enter= Beleg öffnen
//   Pfeil auf/ab     = Markierung bewegen
// Die Aktionsleiste links wirkt immer auf die ZULETZT markierte Zeile.

(function () {
  "use strict";

  const wurzel = document.getElementById("ueb");
  if (!wurzel) return;
  const aktionen = document.getElementById("ueb-aktionen");

  let zuletzt = null; // zuletzt markierte Zeile (Ziel der Aktionen)

  function zeilen() {
    return Array.from(wurzel.querySelectorAll("tr.wp-ueb-zeile"));
  }
  function markierte() {
    return zeilen().filter((z) => z.classList.contains("markiert"));
  }

  function parseBetrag(text) {
    const n = parseFloat(String(text || "0").replace(",", "."));
    return isNaN(n) ? 0 : n;
  }
  function formatEuro(n) {
    return n
      .toFixed(2)
      .replace(".", ",")
      .replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  // ---- Aktionsleiste: URLs mit der ID der markierten Zeile befüllen ---------
  function setzeAktionen(zeile) {
    if (!aktionen) return;
    aktionen.querySelectorAll("[data-aktion]").forEach(function (el) {
      const aktion = el.dataset.aktion;
      const vorlage = el.dataset.url || "";
      let erlaubt = !!zeile;
      if (zeile) {
        if (aktion === "loeschen") erlaubt = zeile.dataset.entwurf === "1";
        if (aktion === "folgebeleg") erlaubt = zeile.dataset.folgebeleg === "1";
        if (aktion === "abschlag") erlaubt = zeile.dataset.folgebeleg === "1";
        if (aktion === "auftrag-erhalten" || aktion === "auftrag-nicht-erhalten")
          erlaubt = zeile.dataset.angebotOffen === "1";
        if (aktion === "erledigt") erlaubt = zeile.dataset.erledigbar === "1";
      }
      const url = zeile
        ? vorlage.replace("{id}", zeile.dataset.id).replace("{adresse_id}", zeile.dataset.adresseId)
        : "#";
      if (el.tagName === "A") {
        el.href = erlaubt ? url : "#";
        el.setAttribute("aria-disabled", erlaubt ? "false" : "true");
        el.classList.toggle("disabled", !erlaubt);
      } else if (el.tagName === "FORM") {
        el.action = erlaubt ? url : "";
        const btn = el.querySelector("button");
        if (btn) btn.disabled = !erlaubt;
      } else {
        el.disabled = !erlaubt;
        el.dataset.ziel = erlaubt ? url : "";
      }
    });
  }

  // ---- Summenzeile ("n von m markiert, Summe x von y") ----------------------
  function aktualisiereSumme() {
    const m = markierte();
    const anzahl = document.getElementById("ueb-markiert-anzahl");
    const summe = document.getElementById("ueb-markiert-summe");
    if (anzahl) anzahl.textContent = String(m.length);
    if (summe) summe.textContent = formatEuro(m.reduce((s, z) => s + parseBetrag(z.dataset.brutto), 0));
  }

  function markiere(zeile, zusaetzlich) {
    if (!zusaetzlich) {
      zeilen().forEach(function (z) {
        z.classList.remove("markiert");
        z.setAttribute("aria-selected", "false");
      });
      zeile.classList.add("markiert");
      zeile.setAttribute("aria-selected", "true");
    } else {
      const neu = !zeile.classList.contains("markiert");
      zeile.classList.toggle("markiert", neu);
      zeile.setAttribute("aria-selected", neu ? "true" : "false");
    }
    zuletzt = zeile.classList.contains("markiert") ? zeile : markierte().slice(-1)[0] || null;
    setzeAktionen(zuletzt);
    aktualisiereSumme();
  }

  function oeffne(zeile) {
    if (zeile) window.location.href = "/belege/detail/" + zeile.dataset.id;
  }

  // ---- Ereignisse ----------------------------------------------------------
  wurzel.addEventListener("click", function (e) {
    const zeile = e.target.closest && e.target.closest("tr.wp-ueb-zeile");
    if (!zeile) return;
    if (e.target.closest("a")) return; // Links (Nummer, Folgebeleg, PDF) normal folgen lassen
    markiere(zeile, e.ctrlKey || e.metaKey);
  });

  wurzel.addEventListener("dblclick", function (e) {
    const zeile = e.target.closest && e.target.closest("tr.wp-ueb-zeile");
    if (zeile) oeffne(zeile);
  });

  wurzel.addEventListener("keydown", function (e) {
    const zeile = e.target.closest && e.target.closest("tr.wp-ueb-zeile");
    if (!zeile) return;
    if (e.key === "Enter") {
      e.preventDefault();
      oeffne(zeile);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const alle = zeilen();
      const i = alle.indexOf(zeile) + (e.key === "ArrowDown" ? 1 : -1);
      if (i >= 0 && i < alle.length) {
        alle[i].focus();
        markiere(alle[i], false);
      }
    } else if (e.key === " ") {
      e.preventDefault();
      markiere(zeile, true);
    }
  });

  // Aktionen, die einen HTMX-Dialog laden (z. B. "Auftrag erhalten"): Die URL
  // steht erst nach dem Markieren fest, deshalb hier per htmx.ajax statt hx-get.
  if (aktionen) {
    aktionen.addEventListener("click", function (e) {
      const btn = e.target.closest && e.target.closest("button[data-aktion='auftrag-erhalten']");
      if (!btn || btn.disabled || !btn.dataset.ziel) return;
      const zurueck = wurzel.dataset.zurueck || "";
      const url = btn.dataset.ziel + "?zurueck=" + encodeURIComponent(zurueck);
      if (window.htmx) {
        window.htmx.ajax("GET", url, { target: "#ueb-dialog", swap: "innerHTML" });
      }
    });
    // Deaktivierte Links (href="#") nicht springen lassen
    aktionen.addEventListener("click", function (e) {
      const a = e.target.closest && e.target.closest("a[aria-disabled='true']");
      if (a) e.preventDefault();
    });
  }

  // Nach jedem Nachladen der Tabelle (Filter, Blättern) Markierung zurücksetzen.
  document.body.addEventListener("htmx:afterSwap", function (e) {
    if (e.target && e.target.id === "uebersicht-body") {
      zuletzt = null;
      setzeAktionen(null);
      aktualisiereSumme();
    }
  });

  setzeAktionen(null);
  aktualisiereSumme();
})();
