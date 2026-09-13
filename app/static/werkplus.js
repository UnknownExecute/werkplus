// WerkPlus – clientseitige Helfer (Kalkulation + mehrzeilige Positionsbeschreibung)

// ---- CSRF-Schutz: Token aus <meta> in jede schreibende Anfrage einspeisen ----
function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return el ? el.getAttribute("content") : "";
}

// HTMX-Requests: Token als Header X-CSRF-Token mitsenden.
document.body.addEventListener("htmx:configRequest", function (e) {
  const t = csrfToken();
  if (t) e.detail.headers["X-CSRF-Token"] = t;
});

// Native <form method="post">: verstecktes Feld csrf_token injizieren (falls fehlt).
function injectCsrfFields(root) {
  const t = csrfToken();
  if (!t) return;
  (root || document).querySelectorAll("form").forEach(function (f) {
    const m = (f.getAttribute("method") || "get").toLowerCase();
    if (m !== "post") return;
    if (f.querySelector('input[name="csrf_token"]')) return;
    const i = document.createElement("input");
    i.type = "hidden";
    i.name = "csrf_token";
    i.value = t;
    f.appendChild(i);
  });
}
document.addEventListener("DOMContentLoaded", function () { injectCsrfFields(document); });
// Auch in per HTMX nachgeladene Formulare (Dialoge, Partials).
document.body.addEventListener("htmx:afterSwap", function (e) { injectCsrfFields(e.target); });

// ---- Bestätigungsdialoge für unumkehrbare Aktionen (native Formulare) ----
// HTMX-Aktionen nutzen hx-confirm. Native <form method="post"> tragen stattdessen
// eines dieser data-Attribute — ein zentraler Listener genügt, kein Inline-JS:
//   data-bestaetigen="Text"      am <form> ODER am auslösenden Submit-Button
//                                (Button gewinnt, damit ein Formular mit
//                                 "Speichern" + "Löschen" nur beim Löschen fragt)
//   data-bestaetigen-aus="Text"  an einer Checkbox: fragt nur, wenn sie beim
//                                Absenden abgewählt wurde, aber serverseitig
//                                gesetzt war (z. B. "Aktiv" → deaktivieren)
function bestaetigungstexte(form, submitter) {
  const texte = [];
  const vomButton = submitter && submitter.dataset ? submitter.dataset.bestaetigen : null;
  if (vomButton) {
    texte.push(vomButton);
  } else if (form.dataset && form.dataset.bestaetigen) {
    texte.push(form.dataset.bestaetigen);
  }
  form.querySelectorAll("[data-bestaetigen-aus]").forEach(function (el) {
    // defaultChecked = Zustand aus dem gelieferten HTML (also serverseitig aktiv)
    if (el.defaultChecked && !el.checked) texte.push(el.dataset.bestaetigenAus);
  });
  return texte;
}

// Ein einziger Listener in der Capture-Phase. Er bricht das Absenden NUR ab,
// wenn der Nutzer ablehnt; sonst läuft alles unverändert weiter (die
// CSRF-Injektion hängt nicht am submit-Event, sondern an DOMContentLoaded bzw.
// htmx:afterSwap und bleibt davon unberührt).
document.addEventListener(
  "submit",
  function (e) {
    const form = e.target;
    if (!form || form.tagName !== "FORM") return;
    const texte = bestaetigungstexte(form, e.submitter);
    for (let i = 0; i < texte.length; i++) {
      if (!window.confirm(texte[i])) {
        e.preventDefault();
        e.stopPropagation();
        return;
      }
    }
  },
  true
);

// Deutsches Dezimalformat ("1.234,56") robust parsen.
function parseDe(text) {
  if (text === null || text === undefined) return 0;
  const s = String(text).trim().replace(/\./g, "").replace(",", ".");
  const n = parseFloat(s);
  return isNaN(n) ? 0 : n;
}

// Zahl als deutschen String mit 2 Nachkommastellen formatieren.
function formatDe(n) {
  return n.toFixed(2).replace(".", ",");
}

// ---- Artikel-Kalkulation live: Listenpreis − Rab1 − Rab2 = EK; EK + Aufschlag = VK ----
// Feld data-kalk="vk" ist überschreibbar: wird es direkt geändert, wird der
// Aufschlag daraus rückgerechnet. Alle anderen Kalkulationsfelder rechnen VK neu.
function initArtikelKalkulation() {
  const form = document.querySelector("[data-kalk-form]");
  if (!form) return;
  const feld = (name) => form.querySelector(`[data-kalk="${name}"]`);
  const ust = () => parseDe(feld("ust") ? feld("ust").value : "20");

  function rechne(quelle) {
    const lpNetto = parseDe(feld("listenpreis_netto").value);
    const lpBrutto = parseDe(feld("listenpreis_brutto").value);
    const u = ust();

    // Listenpreis netto/brutto koppeln — je nachdem, was zuletzt geändert wurde
    let netto = lpNetto;
    if (quelle === "listenpreis_brutto") {
      netto = lpBrutto / (1 + u / 100);
      feld("listenpreis_netto").value = formatDe(netto);
    } else {
      feld("listenpreis_brutto").value = formatDe(netto * (1 + u / 100));
    }

    const rab1 = parseDe(feld("rabatt1").value);
    const rab2 = parseDe(feld("rabatt2").value);
    const ek = netto * (1 - rab1 / 100) * (1 - rab2 / 100);
    feld("ek").value = formatDe(ek);

    if (quelle === "vk") {
      // VK manuell → Aufschlag rückrechnen
      const vk = parseDe(feld("vk").value);
      const aufschlag = ek > 0 ? (vk / ek - 1) * 100 : 0;
      feld("aufschlag").value = formatDe(aufschlag);
      feld("vk_brutto").value = formatDe(vk * (1 + u / 100));
    } else {
      const aufschlag = parseDe(feld("aufschlag").value);
      const vk = ek * (1 + aufschlag / 100);
      feld("vk").value = formatDe(vk);
      feld("vk_brutto").value = formatDe(vk * (1 + u / 100));
    }
  }

  form.querySelectorAll("[data-kalk]").forEach((el) => {
    const rolle = el.getAttribute("data-kalk");
    if (rolle === "ek" || rolle === "vk_brutto") return; // reine Ausgaben
    el.addEventListener("input", () => rechne(rolle));
    el.addEventListener("change", () => rechne(rolle));
  });
}

// ---- Positionsbeschreibung: Enter bestätigt (blur → HTMX-change), Shift+Enter = neue Zeile ----
document.addEventListener("keydown", function (e) {
  const el = e.target;
  if (el.matches && el.matches("textarea.pos-mehrzeilig") && e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    el.blur();
  }
});

// Textareas an ihren Inhalt anpassen (auto-grow).
function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}
document.addEventListener("input", function (e) {
  if (e.target.matches && e.target.matches("textarea.pos-mehrzeilig")) autoGrow(e.target);
});

function initAutoGrow() {
  document.querySelectorAll("textarea.pos-mehrzeilig").forEach(autoGrow);
}

// ---- Hell/Dunkel-Umschalter (Theme in localStorage merken) ----
function initThemeToggle() {
  const btn = document.getElementById("wpThemeToggle");
  if (!btn) return;
  btn.addEventListener("click", function () {
    const html = document.documentElement;
    const neu = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
    html.setAttribute("data-bs-theme", neu);
    try { localStorage.setItem("werkplus-theme", neu); } catch (e) { /* ignore */ }
  });
}

// ---- CSP-sichere Ersatz für hx-on (die CSP verbietet eval, das hx-on braucht) ----
// data-reset-nach-request         am <form>: nach erfolgreichem HTMX-Request zurücksetzen
// data-leeren-nach-request="#id"  am Element: nach erfolgreichem Request Ziel leeren
function initNachRequest() {
  document.body.addEventListener("htmx:afterRequest", function (e) {
    const el = e.detail && e.detail.elt;
    if (!el || !e.detail.successful) return;
    const form = el.closest ? el.closest("form[data-reset-nach-request]") : null;
    if (form) form.reset();
    const quelle = el.closest ? el.closest("[data-leeren-nach-request]") : null;
    if (quelle) {
      const ziel = document.querySelector(quelle.dataset.leerenNachRequest);
      if (ziel) ziel.innerHTML = "";
    }
  });
}

// ---- Globaler Ladebalken: Body während HTMX-Requests markieren ----
function initLadebalken() {
  document.body.addEventListener("htmx:beforeRequest", function () {
    document.body.classList.add("htmx-request");
  });
  document.body.addEventListener("htmx:afterRequest", function () {
    document.body.classList.remove("htmx-request");
  });
}

document.addEventListener("DOMContentLoaded", function () {
  initArtikelKalkulation();
  initAutoGrow();
  initThemeToggle();
  initLadebalken();
  initNachRequest();
});
// Nach jedem HTMX-Swap (z. B. neu gerendertes Positionsgrid) erneut anwenden.
document.body.addEventListener("htmx:afterSwap", initAutoGrow);
