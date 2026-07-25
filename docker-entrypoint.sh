#!/bin/sh
set -e

# Datenbankschema aktualisieren (idempotent)
uv run alembic upgrade head

# Produktions-Erstinitialisierung: Standardrollen, Standardlager, Admin-Benutzer
# (idempotent — legt nur an, was fehlt). KEINE Demo-Daten.
uv run python -m scripts.init_prod

# Anwendungsserver starten
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
