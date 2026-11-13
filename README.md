# Felt Ledger — Django Poker Session Tracker

A responsive poker session journal built with Django class-based views, SQLite, authentication, server-persisted timers, and an SVG results dashboard.

## Run it

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

Open http://127.0.0.1:8000/ and click **Create an account**.

## Test

```bash
python manage.py test
```

## Features

- Sign up / sign in / sign out; data segregated by account.
- Class-based `ListView`, `DetailView`, `CreateView`, `UpdateView`, `DeleteView`.
- Class-based POST-only pause, resume, finish transitions.
- Timer uses persisted seconds plus the last resume timestamp in the database. The browser only renders a live display.
- On finishing, enter cash-out and calculate P/L = cash-out − buy-in.
- Dashboard shows net profit, winning/losing session counts, win rate, and cumulative profit chart.
- SQLite for zero-config local development.

## Resell Ledger (`/resell/`)

Inventory and bookkeeping for reselling, for superusers and members of the "Resell" group. Items get a bin slot and a permanent SKU (`A16-1047`) when saved; the lowest open slot is suggested, and bins, capacities and the item counter are set in the admin under **Bin and SKU settings**. Each item moves through listed → sold → shipped (with returns and write-offs), and 24 reports cover sales, inventory, expenses, mileage, P&L and Schedule C, each downloadable as a PDF. The slot rules live in `resell/locations.py`, the report numbers in `resell/reports.py`.

## Timer design

`accumulated_seconds` stores completed running intervals. While running, elapsed time is accumulated seconds plus `now - resumed_at`. When paused or finished, that current interval is committed to stored seconds. Refreshing the page or closing the browser does not reset the clock.

**Important:** A running session continues counting even with the browser closed until it is paused or finished. Running intervals are rounded down to whole seconds at each state transition. Each user can have more than one active session.

## Money/accounting assumption

For this MVP, `buy_in` is the total amount invested (including rebuys) and `cash_out` is total chips cashed out, in dollars. Final profit is computed only for finished sessions. Break-even sessions are separate from wins and losses.

## Deployment (Fly.io)

The repo includes `Dockerfile`, `start.sh` and `fly.toml`. The app runs one machine with a volume mounted at `/data` that holds the SQLite database and uploaded photos/receipts. `start.sh` runs `migrate` on that machine at every boot, then starts gunicorn. WhiteNoise serves static files; uploads are served only to signed-in users.

First deploy:

```bash
fly launch --copy-config --no-deploy --name <your-app-name>   # creates the app, keeps this fly.toml
fly secrets set DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')"
fly deploy --ha=false                                           # one machine = one database
fly ssh console -C "python manage.py createsuperuser"
```

Later deploys: `fly deploy`. Keep the app at one machine (`fly scale count 1`); each extra machine would get its own volume and a separate database. Fly snapshots volumes daily; for an on-demand backup run `fly ssh sftp get /data/db.sqlite3`.

### Photos and backups in Cloudflare R2

`fly.toml` names the R2 endpoint and bucket. Once the keys are set, new photos are stored in R2 under `resell-images/<upload date>/<SKU>_<name>.jpg` and receipts under `resell-images/receipts/<upload date>/` (still served only to signed-in resell users, through `/media/`), and the database is backed up to `resell-backups/` within an hour of any change. Backups are kept for 2 days, then one per day for 90 days. Without the keys, everything stays on the volume.

```bash
fly secrets set R2_ACCESS_KEY_ID=… R2_SECRET_ACCESS_KEY=…     # an R2 API token with read & write on the bucket
fly ssh console -C "python manage.py copy_media_to_r2"         # once: copy existing photos/receipts to R2
fly ssh console -C "python manage.py backup_db"                 # a backup right now
```

To restore: download a `db-….sqlite3.gz` from the bucket in the Cloudflare dashboard and `gunzip` it. Upload it next to the live database with `fly ssh sftp shell` (`put db.sqlite3 /data/restore.sqlite3`), swap it in with `fly ssh console -C "mv /data/restore.sqlite3 /data/db.sqlite3"`, then `fly apps restart`.
