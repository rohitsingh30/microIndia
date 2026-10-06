# Read-only hosted snapshot of the microIndia dashboard: the API (standard library only) serving the
# built dashboard and a baked-in database snapshot. Writes are refused (MICROINDIA_READ_ONLY=1).
FROM python:3.12-slim

WORKDIR /app
COPY apps/scraper/src /app/apps/scraper/src
COPY apps/dashboard/dist /app/apps/dashboard/dist
COPY deploy/out/microindia-snapshot.sqlite3.gz /tmp/snapshot.gz

RUN useradd --uid 10001 --create-home app \
    && mkdir -p /srv/microindia \
    && gunzip -c /tmp/snapshot.gz > /srv/microindia/snapshot.sqlite3 \
    && rm /tmp/snapshot.gz \
    && chown -R app:app /srv/microindia

USER app
ENV PYTHONPATH=/app/apps/scraper/src \
    PYTHONUNBUFFERED=1 \
    MICROINDIA_READ_ONLY=1
EXPOSE 8080
CMD ["python", "-m", "microindia_scraper.api", "--database", "/srv/microindia/snapshot.sqlite3", \
     "--host", "0.0.0.0", "--port", "8080", "--dist", "/app/apps/dashboard/dist", \
     "--health-file", "/tmp/health.json"]
