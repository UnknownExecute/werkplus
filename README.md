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
5. **Updates**: neuen Code holen (`git pull`) und `sudo docker compose up -d --build`.
   Die Datenbank in `./data` bleibt erhalten; Alembic migriert automatisch.

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

## Betrieb & Sicherheit

- **Produktionsstart**: `WERKPLUS_ENV=production` setzen. Dann verweigert die App den
  Start, solange `WERKPLUS_SECRET_KEY` noch der Dev-Default (oder zu kurz) ist —
  einen sicheren Schlüssel erzeugen, z. B. `python -c "import secrets; print(secrets.token_hex(32))"`.
- **Brute-Force-Schutz**: Nach mehreren Fehlversuchen wird der betroffene Login
  kurzzeitig gesperrt (In-Memory, pro Prozess).
- **Backup**: Die gesamte Anwendungsdatenbank ist die SQLite-Datei (Standard
  `werkplus.db`, im Docker-Volume `/data/werkplus.db`). Für ein konsistentes Backup
  im laufenden Betrieb (WAL-Mode) den SQLite-eigenen Befehl nutzen statt roh zu kopieren:

  ```bash
  sqlite3 werkplus.db ".backup 'backup/werkplus-$(date +%F).db'"
  ```

  Als Cron-/Task-Job täglich ausführen und die Backups extern sichern. Restore =
  gesicherte Datei zurückkopieren (App gestoppt).

## Fachliche Eckpunkte

- Belegkette Verkauf: Angebot → Auftrag → Lieferschein → Rechnung → (Gutschrift)
- Belegkette Einkauf: Bestellung → EK-Lieferschein (Wareneingang) → EK-Rechnung
- Statusmaschine je Beleg: Entwurf → Gedruckt → Erledigt/Storniert; gedruckte
  Rechnungen sind unveränderlich (nur Storno/Gutschrift)
- Nummernkreise je Belegart und Jahr (z. B. `RE-2026-0001`), transaktionssicher
- Lieferschein bucht Lager ab und setzt Seriennummern-Status; Wareneingang bucht zu
  und legt Seriennummern an; jede Buchung im Lagerbewegungs-Journal
- Nur Regelbesteuerung (AT): 20 % / 10 % / 13 %, Rechnung nach § 11 UStG
