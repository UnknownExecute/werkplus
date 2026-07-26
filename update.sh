#!/bin/sh
# WerkPlus-Update: holt den aktuellen Stand von GitHub und startet den Container neu.
# Aufruf auf der NAS im Projektordner:  sudo ./update.sh
#
# Die Datenbank (./data) und die Konfiguration (.env) bleiben unangetastet —
# ueberschrieben werden nur die Programmdateien. Alembic migriert das Schema
# beim Container-Start automatisch.
set -e
cd "$(dirname "$0")"

REPO=$(grep '^WERKPLUS_GITHUB_REPO=' .env | cut -d= -f2-)
TOKEN=$(grep '^WERKPLUS_GITHUB_TOKEN=' .env | cut -d= -f2-)
REPO=${REPO:-UnknownExecute/werkplus}

if [ -z "$TOKEN" ]; then
  echo "Kein WERKPLUS_GITHUB_TOKEN in .env gefunden — Abbruch."
  exit 1
fi

echo "==> Hole aktuellen Code von GitHub ($REPO) ..."
curl -fsSL -H "Authorization: Bearer $TOKEN" \
  "https://api.github.com/repos/$REPO/tarball/main" | tar xz --strip-components=1

echo "==> Baue Image und starte Container neu ..."
docker compose up -d --build

echo "==> Fertig. Aktueller Stand:"
docker compose ps
