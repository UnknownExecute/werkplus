FROM python:3.12-slim

# WeasyPrint-Systemabhängigkeiten (Pango/Cairo) für die Beleg-PDFs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /werkplus
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts ./scripts
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Persistente Daten (SQLite-DB) unter /data; Bug-Screenshots unter static/bugreports
ENV WERKPLUS_DATABASE_URL=sqlite:////data/werkplus.db
ENV WERKPLUS_ENV=production
VOLUME /data
EXPOSE 8000

# Migration + Produktions-Init (Rollen, Standardlager, Admin) — KEINE Demo-Daten,
# dann Server starten. Der Seed mit Demo-Daten läuft nur in der lokalen Entwicklung.
CMD ["./docker-entrypoint.sh"]
