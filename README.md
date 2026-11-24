# Frappe/ERPNext Management Platform

A self-hosted, web-based management platform for deploying, configuring,
monitoring, and maintaining Frappe and ERPNext sites, inspired by Frappe
Cloud, running entirely via Docker Compose.

This is a v1: single server/host, no multi-server fleet, billing, or
marketplace. It supports **multiple Frappe benches pinned to different
Frappe/ERPNext versions**, each hosting any number of sites.

## Architecture at a glance

- **Backend**: Django + Django REST Framework (`api/`), with Celery
  (`worker`) handling every long-running operation asynchronously.
- **Frontend**: React + TypeScript + Tailwind (`web/`), built into the
  `proxy` image and served by nginx.
- **Proxy**: nginx (`proxy/`), the *only* container with a port published
  to the host. Routes the platform UI/API on its own hostname, and routes
  every Frappe/ERPNext site to its bench by hostname.
- **Bench units**: each `Bench` (a Frappe install pinned to one branch,
  e.g. `version-15`) is **3 containers created on demand** by the platform
  via the Docker API, not declared in `docker-compose.yml`:
  - `bench-{id}`: the Frappe bench itself (web + socketio + Frappe's own
    background workers/scheduler, all started by `bench start`)
  - `redis-cache-{id}`, `redis-queue-{id}`: dedicated per bench, so one
    bench's background jobs can never cross-talk with another's
  - a named Docker volume holding that bench's `sites/`/`apps/` dirs

  All sites on all benches share one `mariadb` service.

## Prerequisites

- Docker Engine + the Compose plugin (`docker compose version`)
- Nothing else: Python/Node toolchains are only needed if you want to
  develop outside the containers (see below)

## Running it

```bash
cp .env.example .env
# edit .env: set real passwords/secrets, and your admin login
docker compose build
docker compose up -d
docker compose ps   # wait for postgres/mariadb/platform-redis to be healthy
```

Open `http://platform.localhost/` (or `http://localhost/`) and log in with
the `ADMIN_EMAIL`/`ADMIN_PASSWORD` from your `.env`.

1. **Benches** → create a bench, pick a Frappe branch (e.g. `version-15`).
   Wait for its job to finish (first boot runs `bench init`, which takes a
   few minutes).
2. **New site** → pick that bench, give it a hostname (e.g.
   `demo.localhost`; modern OS/browsers resolve `*.localhost` to
   `127.0.0.1` automatically, no `/etc/hosts` editing needed), optionally
   select apps from the catalog (add ERPNext etc. under **App catalog**
   first: name `erpnext`, repo `https://github.com/frappe/erpnext`).
3. Once the job succeeds, open the site from its detail page.

For a full walkthrough of every page and feature with screenshots (benches,
sites, backups/restore, users, SQL playground, and more), see
[`docs/HOW_TO_GUIDE.md`](docs/HOW_TO_GUIDE.md).

## Known v1 limitations (by design, not oversights)

- **`/var/run/docker.sock` is mounted into `api` and `worker`.** This grants
  real host-level power: anyone who can reach the authenticated platform
  API can, transitively, run arbitrary containers on this host. Acceptable
  for a trusted single-admin tool; `proxy` is the only container with a
  published port, so this is never exposed directly to the network. Don't
  expose the platform API itself to an untrusted network without additional
  auth hardening.
- **"Enable/disable maintenance mode", not "start/stop".** A bench
  container hosts multiple sites, so there's no per-site container to
  stop. The UI deliberately avoids implying one exists.
- **"Update app" is bench-wide, not site-wide.** Apps live in one shared
  `apps/` directory per bench, so updating an app affects every site on
  that bench at once. The UI lists affected sites before you confirm.
  "Migrate" (per-site) is independent and safe to run on its own.
- **Per-bench `redis-queue` is ephemeral** (no persistent volume). A
  container restart loses any of Frappe's own queued background jobs
  (emails, scheduled reports) for that bench, not platform-level jobs,
  which live in Postgres. Mirrors Frappe's normal operational character.
- **Site admin passwords are stored in plaintext** in the platform's own
  Postgres database so they can be shown back to you after creation. Treat
  the platform database as sensitive; it is never exposed over the API
  after site creation.
- **No Alembic-equivalent concerns**: Django's built-in migrations are
  used from day one, so schema evolution is a non-issue (unlike an
  earlier SQLAlchemy-based draft of this design).
- Bench web process uses werkzeug's single-threaded dev server by default
  (via `bench start`), not gunicorn. Fine for a self-hosted low-concurrency
  tool; swap the bench Procfile's `web` line for `gunicorn` if you need
  more throughput per site.

## Developing without rebuilding containers every time

Backend:

```bash
cd api
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
DATABASE_URL="sqlite:///$(pwd)/dev.sqlite3" ADMIN_PASSWORD=dev ./.venv/bin/python manage.py migrate
DATABASE_URL="sqlite:///$(pwd)/dev.sqlite3" ADMIN_EMAIL=admin@example.com ADMIN_PASSWORD=dev ./.venv/bin/python manage.py seed_admin
DATABASE_URL="sqlite:///$(pwd)/dev.sqlite3" ADMIN_PASSWORD=dev ./.venv/bin/python manage.py test
```

Frontend:

```bash
cd web
npm install
npm run dev   # proxies /api to http://localhost:8000, see vite.config.ts
```

## Repo layout

```
api/            Django project (accounts, benches, sites apps)
bench_image/    Image used for every dynamically created bench container
proxy/          nginx + the built React app (the only published port)
web/            React/TypeScript frontend source
docker-compose.yml   Static platform services (postgres, mariadb, redis,
                     api, worker, proxy); bench units are created at
                     runtime via the Docker API, not declared here.
```
