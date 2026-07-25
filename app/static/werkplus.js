// WerkPlus – clientseitige Helfer (Kalkulation + mehrzeilige Positionsbeschreibung)

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
});
// Nach jedem HTMX-Swap (z. B. neu gerendertes Positionsgrid) erneut anwenden.
document.body.addEventListener("htmx:afterSwap", initAutoGrow);
