# WerkPlus

CRM + schlankes ERP für Handwerksbetriebe (Heizung, Elektro, Kälte) — inspiriert von der
Beleglogik klassischer Handwerkersoftware (Kplus): durchgängige Belegkette, Artikel- und
Lagerverwaltung mit Seriennummern, Anlagenverwaltung je Kunde.

Single-tenant, on-premise, serverseitig gerendert (FastAPI + Jinja2 + HTMX), SQLite.

## Setup (lokal, ≤ 5 Befehle)

Voraussetzung: Python 3.12 und [uv](https://docs.astral.sh/uv/) (`pip install uv`).

```bash
uv sync
uv run alembic upgrade head
uv run python -m scripts.seed
uv run uvicorn app.main:app
```

Danach: <http://127.0.0.1:8000> — Logins: `admin`/`admin` (Admin), `sb`/`sb` (Sachbearbeiter).

Hinweis Windows: WeasyPrint (Beleg-PDFs) benötigt die GTK3-Runtime
(<https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer>).
Ohne sie läuft die App; nur der PDF-Druck ist deaktiviert (PDF-Tests werden übersprungen).

## Alternativ: Docker

```bash
docker compose up
```

Migriert, initialisiert (Rollen, Standardlager, Admin — **keine** Demo-Daten) und
startet auf Port 8000 (Host-Port laut `docker-compose.yml`, Standard 8080).

## Produktivbetrieb auf einer Synology NAS (Docker)

Voraussetzung: Synology mit **Container Manager** (früher „Docker"-Paket), x86-Modell.

1. **Code auf die NAS bringen** — entweder das private Git-Repo klonen (siehe unten)
   oder den Projektordner via File Station kopieren, z. B. nach
   `/volume1/docker/werkplus`.
2. **Konfiguration anlegen**: `.env.example` nach `.env` kopieren und ausfüllen —
   v. a. einen sicheren `WERKPLUS_SECRET_KEY` und `WERKPLUS_ADMIN_PASSWORT`.
   Optional den GitHub-Token/Repo für den Bug-Melder.
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"   # Secret-Key erzeugen
   ```
3. **Starten** (per SSH auf der NAS, im Projektordner):
   ```bash
   mkdir -p data/bugreports    # Synology legt Bind-Mount-Ordner nicht selbst an
   sudo docker compose up -d --build
   ```
   Container Manager zeigt das Projekt danach unter „Projekt" an; Start/Stopp geht
   auch über die Oberfläche.
4. **Aufrufen**: `http://<NAS-IP>:9999` — erster Login `admin` mit dem in `.env`
   gesetzten Passwort. Passwort danach in der App unter *Mitarbeiter* ändern.
5. **Updates**: Ein `git push` aktualisiert nur GitHub — die NAS holt den neuen
   Stand nicht von selbst. Auf der NAS im Projektordner genügt dafür ein Befehl:
   ```bash
   sudo ./update.sh
   ```
   Das Skript lädt den aktuellen `main`-Stand von GitHub (Token aus `.env`), baut
   das Image neu und startet den Container. Die Datenbank in `./data` und die
   `.env` bleiben erhalten; Alembic migriert das Schema beim Start automatisch.

Die Daten liegen dauerhaft im NAS-Ordner `./data` (SQLite-DB + Bug-Screenshots) —
in die Synology-Backup-Aufgaben (Hyper Backup) mit aufnehmen.

## Mit dem privaten GitHub-Repo verbinden

Das Repo ist bereits lokal initialisiert und committet. Zum Anbinden an dein
privates GitHub-Repo (einmalig):

```bash
git remote add origin git@github.com:<dein-account>/<dein-repo>.git
git branch -M main
git push -u origin main
```

Danach genügt `git push` für neue Änderungen und `git pull` auf der NAS zum Ausrollen.

## Entwicklung

```bash
uv run ruff check .
uv run pytest
```

- `app/models/` — SQLModel-Entities (Beträge als `Decimal`, nie float)
- `app/services/` — Geschäftslogik (Statusmaschine, Nummernkreise, Lagerbuchung, PDF)
- `app/routers/` — dünne Routen, serverseitiges Rendering
- `app/templates/` — Jinja2 (+ `templates/pdf/` für Belegdruck)
- `alembic/` — Migrationen (`uv run alembic revision --autogenerate -m "..."`)

### Continuous Integration

`.github/workflows/ci.yml` läuft bei **jedem Push auf `main` und bei jedem Pull
Request** (ubuntu-latest, Python 3.12) und prüft:

1. `uv sync --dev` — Abhängigkeiten inkl. Dev-Gruppe
2. `uv run ruff check .` — Lint
3. `uv run pytest` — die komplette Testsuite
4. `uv run pytest tests/test_pdf.py` als eigener Schritt

Damit die PDF-Tests nicht stillschweigend übersprungen werden, installiert die CI
vorher die WeasyPrint-Systempakete (Pango/Cairo/gdk-pixbuf, Paketliste sinngemäß
aus dem `Dockerfile`), verifiziert den `import weasyprint` und lässt Schritt 4
**fehlschlagen, wenn ein Test als `SKIPPED` im Log auftaucht** — eine grüne CI
ohne echten PDF-Byte-Test wäre trügerisch. Lokal auf Windows ohne GTK bleibt der
PDF-Byte-Test wie bisher geskippt; das ist erwartet.

### Schneller Inner-Loop

Langsame Tests sind mit pytest-Markern versehen (in `pyproject.toml`
registriert), damit man sie beim Entwickeln überspringen kann:

```bash
uv run pytest -m "not slow"                     # ohne laufzeitintensive Tests
uv run pytest -m "not slow and not integration" # schnellster Durchlauf
uv run pytest                                   # unverändert ALLE Tests (auch in CI)
```

- `slow` — `tests/test_nummernkreis.py::test_race_acht_threads_lueckenlos`
  (dateibasiertes SQLite, 8 Threads). Weitere Nebenläufigkeits-/Race-Tests
  gehören hier ebenfalls hin (z. B. `tests/test_lager_nebenlaeufig.py`).
- `integration` — `tests/test_e2e_durchlauf.py` (großer Durchlauf über alle
  Schichten)

Ohne `-m` ändert sich nichts: `uv run pytest` führt weiterhin die vollständige
Suite aus, und genau das macht auch die CI.

Optionale nächste Schritte (bewusst noch nicht eingerichtet, weil sie den
Bestand umformatieren bzw. lokale Hooks brauchen): `ruff format` samt
`ruff format --check` in der CI und ein pre-commit-Hook für Lint/Format.

### Migrationen beim Deploy

**Migrationen laufen beim Containerstart automatisch.** `docker-entrypoint.sh`
führt vor dem Serverstart `uv run alembic upgrade head` aus (idempotent), danach
`scripts.init_prod`. Beim Ausrollen auf die NAS (`./update.sh`) ist also **kein
manueller Migrationsschritt nötig** — es genügt, dass die neue Revision im Repo
liegt. Konsequenzen fürs Entwickeln:

- Jede Modelländerung braucht eine mitcommittete Alembic-Revision, sonst
  divergiert das Schema auf der NAS von den Models.
- Vor dem Ausrollen ein Backup von `./data/werkplus.db` haben — ein
  fehlgeschlagenes `upgrade head` bricht den Containerstart ab.

> **Warnung — Self-Foreign-Key bei `alembic revision --autogenerate`:**
> Autogenerate erzeugt für den selbst-referenzierenden Fremdschlüssel
> `beleg.auftrag_id → beleg.id` immer wieder `create_foreign_key`-Zeilen (bzw.
> `batch_op.create_foreign_key(...)`). Diese **scheitern im SQLite-Batch-Modus**
> und müssen aus der neu generierten Migration **gelöscht** werden — Spalte und
> Index genügen (SQLite erzwingt Fremdschlüssel ohnehin nur mit aktivem
> `PRAGMA foreign_keys`). So ist es bereits in den Revisionen `a9197f1b1379` und
> `4d89de911e3f` gemacht; dort findet sich auch ein erklärender Kommentar.
> Also: jede autogenerierte Migration vor dem Commit durchlesen.

## Betrieb & Sicherheit

- **Produktionsstart**: `WERKPLUS_ENV=production` setzen. Dann verweigert die App den
  Start, solange `WERKPLUS_SECRET_KEY` noch der Dev-Default (oder zu kurz) ist —
  einen sicheren Schlüssel erzeugen, z. B. `python -c "import secrets; print(secrets.token_hex(32))"`.
- **Brute-Force-Schutz**: Nach mehreren Fehlversuchen wird der betroffene Login
  kurzzeitig gesperrt (In-Memory, pro Prozess).
- **CSRF-Schutz**: Schreibende Anfragen (POST/PUT/DELETE) brauchen einen
  Sitzungs-Token — als Formularfeld `csrf_token` oder Header `X-CSRF-Token`.
  Beides fügt `app/static/werkplus.js` automatisch ein; im Code ist nichts zu
  beachten. Notfalls abschaltbar mit `WERKPLUS_CSRF=0`.
- **Sicherheits-Header**: CSP, `X-Frame-Options: DENY`, `nosniff` und
  `Referrer-Policy` werden auf jede Antwort gesetzt (`app/security.py`).
- **Session-Cookie**: In Produktion wird es nur über HTTPS gesendet.
  ⚠️ Bei direktem HTTP-Zugriff (`http://<NAS-IP>:9999` ohne HTTPS-Reverse-Proxy)
  scheitert dann der Login — in diesem Fall `WERKPLUS_COOKIE_SECURE=0` in die
  `.env` eintragen (besser: HTTPS im Synology-Reverse-Proxy aktivieren).
- **Health-Check**: `GET /health` antwortet ohne Login mit
  `{"status": "ok", "datenbank": "ok"}` (HTTP 200). Ist die Datenbank nicht
  erreichbar (`SELECT 1` schlägt fehl), kommt HTTP 503 mit
  `{"status": "fehler", ...}`. Die Antwort enthält bewusst keine Pfade,
  Versionen oder Konfigurationsdetails; der Grund steht nur im Log. Die
  `docker-compose.yml` nutzt den Endpunkt als `healthcheck`, damit Docker bzw.
  der Synology Container Manager den Zustand kennt.
- **Logging**: Zentral konfiguriert in `app/logging_setup.py`, Ausgabe nach
  stdout — also `docker logs -f werkplus` bzw. Container Manager → Protokoll.
  Level über `WERKPLUS_LOG_LEVEL` (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`,
  Standard `INFO`). Protokolliert werden u. a. erfolgreiche Logins (Login-Name,
  INFO), fehlgeschlagene Logins samt Grund und Zählerstand, ausgelöste Sperren
  (WARNING) sowie fehlgeschlagene GitHub-Übertragungen von Bug-Meldungen.
  Passwörter, Tokens und Secret-Keys werden nie geloggt.
- **Bereichsrechte objektbezogen**: Verkaufs- und Einkaufsbelege sind getrennt
  geschützt; auch Suche und Dashboard zeigen nur erlaubte Bereiche.
- **Admin-Schutz**: `ist_admin` an einer Rolle darf nur ein Admin setzen, und der
  letzte aktive Admin kann nicht deaktiviert/herabgestuft werden.
- **Backup**: Die gesamte Anwendungsdatenbank ist die SQLite-Datei (Standard
  `werkplus.db`, im Docker-Volume `/data/werkplus.db`). Für ein konsistentes Backup
  im laufenden Betrieb (WAL-Mode) den SQLite-eigenen Befehl nutzen statt roh zu kopieren:

  ```bash
  sqlite3 werkplus.db ".backup 'backup/werkplus-$(date +%F).db'"
  ```

  Als Cron-/Task-Job täglich ausführen und die Backups extern sichern. Restore =
  gesicherte Datei zurückkopieren (App gestoppt).

**Brute-Force-Schutz: Limit je Login-Name und je IP.** Fehlversuche werden in
einem 5-Minuten-Fenster doppelt gezählt: 5 pro Login-Name und 20 pro Client-IP
(höher, weil hinter einer NAT-IP mehrere Mitarbeiter sitzen). Das IP-Limit
bremst „Password Spraying“, bei dem ein Passwort gegen viele Login-Namen
probiert wird. Die Zähler liegen im Prozessspeicher, sind auf 1000 verfolgte
Schlüssel begrenzt und gelten je uvicorn-Worker — WerkPlus daher mit **einem**
Worker betreiben (der Docker-Entrypoint startet genau einen). Hinter dem
Synology-Reverse-Proxy steht die echte IP in `X-Forwarded-For`; dieser Header
wird nur mit `WERKPLUS_TRUST_PROXY=1` ausgewertet (sonst gilt die direkte
Verbindungsadresse). ⚠️ `WERKPLUS_TRUST_PROXY=1` nur setzen, wenn WerkPlus
ausschließlich über den Reverse-Proxy erreichbar ist — ist der Port sonst direkt
erreichbar, kann der Header gefälscht und das IP-Limit umgangen werden.

## Fachliche Eckpunkte

- Belegkette Verkauf: Angebot → Auftrag → Lieferschein → Rechnung → (Gutschrift)
- Belegkette Einkauf: Bestellung → EK-Lieferschein (Wareneingang) → EK-Rechnung
- Statusmaschine je Beleg: Entwurf → Gedruckt → Erledigt/Storniert; gedruckte
  Rechnungen sind unveränderlich (nur Storno/Gutschrift)
- Nummernkreise je Belegart und Jahr im Kplus-Format `JJNNNN` (z. B. `260001`),
  transaktionssicher; jede Belegart zählt für sich (Angebot 260001 und Rechnung
  260001 dürfen nebeneinander existieren). Startwert je Belegart unter
  *Einstellungen → Nummernkreise* setzbar (Anschluss an die alte Software)
- Belegübersicht VK/EK im Kplus-Stil: ein Reiter je Belegart, Aktionsleiste
  (Kopfdaten, Positionen, Drucken, Kopieren, Löschen, Folgebeleg, Auftrag
  erhalten / nicht erhalten), Filter (Suchbegriff, Datum, Status, nur eigene,
  Auftragsart), Summenzeile; am Handy als Kartenliste
- Belegkopf: Betreff, Termin, Auftragsart (nur Auftrag: Service-/Bestell-/
  Lieferauftrag), Angebotsausgang (erhalten/nicht erhalten)
- Lieferschein bucht Lager ab und setzt Seriennummern-Status; Wareneingang bucht zu
  und legt Seriennummern an; jede Buchung im Lagerbewegungs-Journal
- Nur Regelbesteuerung (AT): 20 % / 10 % / 13 %, Rechnung nach § 11 UStG
