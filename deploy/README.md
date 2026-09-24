# Deploying to the office server

The target is the `resume-filter` VM (Proxmox VM 104) at **192.168.1.7**.
Everything lives on that one machine — application, database and uploaded CVs
— so a VM snapshot is a complete backup and nothing else has to be switched
on for the tracker to work.

```
          browser on the office LAN
                    │  http://192.168.1.7
                    ▼
   ┌──────────────────────────────────────────┐
   │ resume-filter VM (Ubuntu)                │
   │                                          │
   │  nginx :80 ──► uvicorn 127.0.0.1:8000    │
   │                    │                     │
   │                    ▼                     │
   │            PostgreSQL 127.0.0.1:5432     │
   │            /var/lib/resume-filter/cvs    │
   └──────────────────────────────────────────┘
```

uvicorn binds the loopback interface only, so nginx is the one way in and
port 80 is the only port that has to be open.

---

## First install

Needs SSH on the VM. If it is not there yet, in the Proxmox console run:

```bash
sudo apt install -y openssh-server
```

Then, from a machine holding this repository:

```bash
# tar rather than rsync: Git Bash on Windows ships tar but not rsync.
# On Linux or macOS either works.
tar czf - --exclude='./.venv' --exclude='./data' --exclude='./.git' \
          --exclude='__pycache__' --exclude='./.pytest_cache' --exclude='./.env' . \
  | ssh madu@192.168.1.7 "rm -rf /tmp/resume-filter-src && mkdir -p /tmp/resume-filter-src \
                          && tar xzf - -C /tmp/resume-filter-src"

ssh madu@192.168.1.7 "sudo bash /tmp/resume-filter-src/deploy/install.sh"
```

The installer prints the URL when it has finished, and fails loudly if the
app cannot reach its database rather than leaving a broken service enabled.

## What it puts where

| | |
|---|---|
| `/opt/resume-filter` | application and virtualenv |
| `/opt/resume-filter/.env` | settings, including the generated database password (mode 640) |
| `/var/lib/resume-filter/cvs` | uploaded CVs — **this is the personal data; back it up** |
| `interview_tracker` | database, owned by the `resumefilter` role |
| `resume-filter.service` | uvicorn under systemd, restarted on failure |
| `/etc/nginx/sites-available/resume-filter` | the front end |

The CVs live outside `/opt` on purpose: an upgrade replaces the application
directory wholesale, and the uploads must not go with it.

## Upgrading

The same two commands. `install.sh` is idempotent — it keeps the database,
the uploaded CVs and the generated password, and replaces only the code and
its dependencies.

```bash
# tar rather than rsync: Git Bash on Windows ships tar but not rsync.
# On Linux or macOS either works.
tar czf - --exclude='./.venv' --exclude='./data' --exclude='./.git' \
          --exclude='__pycache__' --exclude='./.pytest_cache' --exclude='./.env' . \
  | ssh madu@192.168.1.7 "rm -rf /tmp/resume-filter-src && mkdir -p /tmp/resume-filter-src \
                          && tar xzf - -C /tmp/resume-filter-src"

ssh madu@192.168.1.7 "sudo bash /tmp/resume-filter-src/deploy/install.sh"
```

## Running it

```bash
sudo systemctl status resume-filter      # is it up
sudo systemctl restart resume-filter     # after editing .env
journalctl -u resume-filter -f           # live logs
curl -s localhost/api/health             # what the UI's status pill reads
```

Sample data, as the service account so the files it writes stay owned
correctly:

```bash
sudo -u resumefilter /opt/resume-filter/.venv/bin/python \
     /opt/resume-filter/seed_demo.py           # add
sudo -u resumefilter /opt/resume-filter/.venv/bin/python \
     /opt/resume-filter/seed_demo.py --clear   # remove again
```

## Backups

Two things matter, and they have to be taken together — a database restored
without its CVs has records pointing at files that are not there:

```bash
sudo -u postgres pg_dump -Fc interview_tracker > tracker-$(date +%F).dump
sudo tar czf cvs-$(date +%F).tar.gz -C /var/lib/resume-filter cvs
```

Restoring:

```bash
sudo systemctl stop resume-filter
sudo -u postgres dropdb interview_tracker
sudo -u postgres createdb -O resumefilter interview_tracker
sudo -u postgres pg_restore -d interview_tracker tracker-YYYY-MM-DD.dump
sudo tar xzf cvs-YYYY-MM-DD.tar.gz -C /var/lib/resume-filter
sudo chown -R resumefilter:resumefilter /var/lib/resume-filter
sudo systemctl start resume-filter
```

A Proxmox snapshot of VM 104 covers both at once and is the easier routine;
the dumps are for when you want a copy off the machine.

## Things worth knowing

**There is no login.** Anyone who can reach 192.168.1.7 can read every
candidate record and download every CV. That is the same trade-off the app
has always made, but it matters more now that it is on an always-on server
rather than somebody's desktop. Keep the VM off any port forward, and if the
office network is shared with guest wifi, put a firewall rule in front of it.

**CORS is wide open** (`allow_origins=["*"]` in `app/main.py`). Harmless while
the app is only reachable from inside the network, worth tightening if that
ever changes.

**It is HTTP, not HTTPS.** Passwords are not involved, but CVs and candidate
details cross the network in the clear. For a LAN-only tool that is usually
accepted; nginx is already in place if you later want a certificate.

**Scanned CVs need OCR**, which is not installed by default because it pulls
~250MB of models:

```bash
sudo -u resumefilter /opt/resume-filter/.venv/bin/pip install rapidocr-onnxruntime
sudo systemctl restart resume-filter
```

Without it a scanned CV uploads fine but comes back with no contact fields,
and the UI says so.

## If it will not start

```bash
journalctl -u resume-filter -n 50 --no-pager
```

| Symptom | Usually |
|---|---|
| Status pill says "Database offline" | `sudo systemctl status postgresql`, then check `PG_PASSWORD` in `.env` matches the role |
| "Interview Tracker is starting" for more than a minute | that page is nginx's 502 handler — the service is not coming up, read the journal |
| 413 on upload | a CV over 25MB; raise `client_max_body_size` **and** `MAX_CV_MB` |
| Service restarting in a loop | usually a dependency that failed to build; `sudo -u resumefilter /opt/resume-filter/.venv/bin/python -c "import app.main"` shows the real error |
