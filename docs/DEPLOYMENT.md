# AgencyOS — Deployment and operations

AgencyOS is an internal tool for one agency. The supported shapes are:

| Shape | Database | Who it suits |
|---|---|---|
| **Your Mac** (native Python) | SQLite | One person, local use. See the README quick start |
| **One server with Docker Compose** | SQLite or PostgreSQL | A small team reaching it over HTTPS |

Run **one app process**. Background agent tasks and rate limiting live in that process, so add
CPU/RAM rather than replicas. (A worker queue and shared rate limiter are the path to more
processes; the code is structured for that, but it is not built.)

---

## 1. Production checklist

Before anyone other than you uses it:

- [ ] `APP_ENV=production` (the Docker image sets this by default)
- [ ] `SECRET_KEY` — a long random value, kept secret and **stable** (changing it signs everyone
      out and invalidates unsubscribe links):
      `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
- [ ] Served over **HTTPS** through a reverse proxy; `PUBLIC_BASE_URL=https://your-domain`
- [ ] `FORWARDED_ALLOW_IPS` set to your proxy's address (so rate limits see real client IPs)
- [ ] `ENABLE_DEMO_LOGIN` unset (the app refuses to start if it is true in production)
- [ ] LLM provider key set for the provider you chose; agency identity (`AGENCY_NAME`,
      `AGENCY_ADDRESS`, `SENDER_NAME`, `SENDER_EMAIL`) filled in
- [ ] A Founder account created from the command line, and its password stored in a password manager
- [ ] Backups scheduled and a restore tested (section 5)
- [ ] `python -m app.cli check-config` prints no warnings (the same list appears under
      **Settings → System status**)

In production the app also: requires `SECRET_KEY` ≥ 32 characters, refuses `DEBUG=true`, marks
cookies `Secure`, sends `Strict-Transport-Security`, and turns off `/docs` and `/openapi.json`
(set `ENABLE_API_DOCS=true` to turn them back on).

---

## 2. Docker Compose on a server

```bash
git clone https://github.com/erpkb-stack/agencyos.git && cd agencyos
cp .env.example .env
# edit .env: APP_ENV=production, SECRET_KEY, PUBLIC_BASE_URL, provider key, agency identity
docker compose up -d --build
docker compose exec agencyos python -m app.cli create-user \
  --email you@youragency.com --name "Your Name" --role FOUNDER
docker compose exec agencyos python -m app.cli check-config
```

The container listens on `127.0.0.1:8000` only; put the reverse proxy in front. Migrations run
automatically on start. Data lives in the `agencyos-data` volume.

### With PostgreSQL

Add to `.env`:

```bash
POSTGRES_PASSWORD=<a long random password>
DATABASE_URL=postgresql+psycopg://agencyos:<same password>@db:5432/agencyos
```

then `docker compose --profile postgres up -d --build`. The app container restarts until the
database is ready on first boot; that is expected.

### Reverse proxy (Caddy example)

Caddy obtains and renews TLS certificates automatically:

```caddyfile
agencyos.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

With Caddy on the same host, `FORWARDED_ALLOW_IPS=127.0.0.1` (the default) is correct. For a proxy
on another machine or in another container, set it to that address.

### nginx example

```nginx
server {
    listen 443 ssl http2;
    server_name agencyos.example.com;
    # ssl_certificate / ssl_certificate_key …
    client_max_body_size 6m;               # app limit is 5 MB (MAX_REQUEST_BYTES)
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;           # agent tasks run in the background; requests stay short
    }
}
```

---

## 3. Native (no Docker) on macOS/Linux

```bash
./scripts/setup.sh            # venv, dependencies, .env with a random SECRET_KEY, database
./scripts/create_founder.sh you@youragency.com "Your Name"
cd backend && ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

`./scripts/run_dev.sh` is for development only (auto-reload). To keep the app running, use your
platform's service manager (systemd on Linux, launchd on macOS) with the command above.

---

## 4. Upgrades

```bash
git pull
docker compose up -d --build          # or: pip install -r requirements.txt && python -m app.cli init-db
```

`init-db` applies new Alembic migrations and adds any new built-in agents without touching your
edits. **Back up before upgrading.**

---

## 5. Backups and restore

### SQLite

```bash
python -m app.cli backup-db                     # native, writes data/backups/agencyos-YYYYMMDD-HHMMSS.db
docker compose exec agencyos python -m app.cli backup-db   # Docker (inside the data volume)
```

The backup uses SQLite's online backup API (safe while the app runs), is integrity-checked,
written with `600` permissions, and the newest 14 are kept (`--keep N`). Copy backups off the
machine (they contain client and prospect data, so store them encrypted).

Schedule daily, e.g. cron on Linux:

```cron
15 2 * * * cd /srv/agencyos && docker compose exec -T agencyos python -m app.cli backup-db
```

**Restore:** stop the app, replace `data/agencyos.db` with the backup file (remove any
`agencyos.db-wal` / `agencyos.db-shm` next to it), start the app.

### PostgreSQL

```bash
docker compose exec db pg_dump -U agencyos -Fc agencyos > agencyos-$(date +%F).dump
# restore into an empty database:
docker compose exec -T db pg_restore -U agencyos -d agencyos --clean < agencyos-2026-11-10.dump
```

---

## 6. Account administration

| Task | How |
|---|---|
| Add a user | Settings → Team (Founder/Admin), or `python -m app.cli create-user …` |
| Deactivate / reactivate | Settings → Team, or `python -m app.cli set-active --email … --inactive` (signs them out everywhere) |
| Forgotten password | `python -m app.cli reset-password --email …` (signs them out everywhere) |
| Locked out after failed logins | Wait 15 minutes (5 failures per account per 15 minutes), or reset the password |

Only a Founder can manage Founder and Admin accounts; the last active Founder cannot be
deactivated or demoted; nobody can change their own role or status.

---

## 7. Monitoring

- Liveness: `GET /api/v1/health` · Readiness (database reachable): `GET /api/v1/health/ready`
  (503 when not). The Docker image has a `HEALTHCHECK` on readiness.
- Logs are JSON lines on stdout with a `request_id` per request (also returned as `X-Request-ID`).
  Configuration warnings are logged at start-up.
- Agent failures, approvals and logins are in the audit log (dashboard activity feeds).

---

## 8. Limits to know

- One process (see above). SQLite is single-writer; fine for a small team, use PostgreSQL for more.
- Request bodies are capped at 5 MB (`MAX_REQUEST_BYTES`); CSV imports at 2 MB / 5,000 rows.
- AgencyOS never sends email, launches ads, hosts landing pages or charges cards. Approved work is
  carried out in your own tools.
