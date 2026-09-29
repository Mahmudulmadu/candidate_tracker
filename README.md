# Interview Tracker

Records interviews and tells you, the moment a CV comes in, whether you have
met this person before — and what happened last time.

The problem it solves: the same candidate applies in March and again eighteen
months later, spells their email `Ayesha.Rahman+jobs@gmail.com` the first time
and `ayesharahman@googlemail.com` the second, writes their phone as
`01711-223344` and then `+880 1711 223344`, and signs the form "Rahman,
Ayesha". Nothing about the two records matches on sight. This app recognises
them as one person and shows you the 2024 feedback that said "revisit in a
year".

---

## Running it

```powershell
.\start.ps1
```

That creates the virtualenv, installs dependencies the first time, and starts
the server. Then open **http://localhost:8000**.

Other machines in the office reach it at `http://<this-machine-ip>:8000` —
`HOST=0.0.0.0` in `.env` is what makes that work. Find the IP with `ipconfig`.

Manual equivalent, if you prefer:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python run.py
```

There is no login and no user accounts — it is meant for an office LAN, not
the public internet.

### Demo data

```powershell
.\.venv\Scripts\python seed_demo.py           # add six sample interviews
.\.venv\Scripts\python seed_demo.py --clear   # remove them again
```

The samples include the Ayesha case above (two applications, one person) and a
different Ayesha Rahman in Finance who must **not** be merged with her.

---

## How it recognises people

Every identity field is normalized before anything is compared, then matched
on a ladder. The confidence decides whether the new interview is filed against
the existing person automatically or whether you are asked first.

| Signal | Confidence | Links automatically? |
|---|---|---|
| Email | 1.00 | yes |
| LinkedIn slug | 0.95 | yes |
| GitHub handle | 0.95 | yes |
| Phone (held by exactly one candidate) | 0.85 | yes |
| Phone (held by several — a shared line) | 0.50 | no, asks |
| Name only | 0.40 | **never** |

The threshold is `IDENTITY_AUTO_LINK_MIN_CONFIDENCE` in `.env`, default 0.85.
Raise it to 0.95 if you would rather phone numbers never linked on their own.

**A name alone never links two records.** That asymmetry is deliberate:
wrongly splitting one person into two records leaves you with a duplicate,
which is annoying; wrongly merging two people puts one candidate's rejection
into a stranger's history, which is much worse. When the matcher is unsure it
shows a "possible match" and lets you decide with one click.

What normalization actually does:

- **Email** — lowercased, `+tags` removed, dots dropped in the local part for
  Gmail only (other providers treat them as significant), `googlemail.com`
  folded to `gmail.com`.
- **Phone** — parsed to E.164 assuming Bangladesh when no country code is
  given (`IDENTITY_DEFAULT_PHONE_REGION`), then matched on the **last 9
  digits**, so `01711223344` and `+8801711223344` agree. A field holding two
  numbers (`01768438600,01992460122`) takes the first.
- **Name** — accents folded, honorifics (`Md.`, `Dr.`) and qualifications
  (`PhD`, `BSc`) dropped, middle initials discarded, then token-sorted, so
  "Rahman, Ayesha" and "Md. Ayesha Rahman" produce the same key.
- **LinkedIn / GitHub** — reduced to the bare slug, ignoring scheme, `www`,
  country subdomains, locale segments, tracking parameters and repository
  paths. Company pages and GitHub's reserved paths (`/orgs/`, `/gist/`) are
  refused, because they are not one person.

Identity keys **accumulate** rather than overwrite. Someone who applied with a
university address in 2023 and a work address today is one person with two
mailboxes, and both keep matching them.

---

## Reading CVs

Drop a PDF, DOCX or TXT on the upload area. Text comes out via PyMuPDF (or
python-docx), then regexes pull out the email, phone, LinkedIn and GitHub —
no LLM, so nothing is invented and nothing is sent anywhere.

The extracted fields **pre-fill the blanks in the form and never overwrite
what you have already typed**, and the duplicate check runs immediately.

Two details worth knowing:

- A URL that PDF extraction split across a line is rejoined. Without that,
  `linkedin.com/in/karim` + `-hossain-98765` truncates to `karim` — and since
  a profile match links automatically, two people whose URLs happened to break
  at the same prefix would be merged.
- **Scanned CVs** (no text layer) need OCR, which is optional because it
  downloads ~250MB of models:
  ```powershell
  .\.venv\Scripts\pip install rapidocr-onnxruntime
  ```
  Without it, a scanned CV uploads fine but comes back with no contact fields,
  and the UI says so.

Uploaded files are stored under `data/cvs/` (`CV_STORAGE_DIR`) and linked from
the interview record. **That folder is personal data — back it up with the
rest of the office share, and note it is excluded from git.**

---

## Data

PostgreSQL, database `interview_tracker`. The database, both tables and their
indexes are created on first run; nothing needs setting up in pgAdmin beyond a
server and a role allowed to create databases.

| Table | Key | Holds |
|---|---|---|
| `candidates` | `id` | one row per **person**, with every identity key they have ever used |
| `interviews` | `id`, `candidate_id` → `candidates(id)` | one row per **interview** |

The foreign key is `ON DELETE CASCADE`, so removing a person takes their
history with them and an orphaned interview cannot exist.

The identity keys a person accumulates (`emails`, `phone_keys`, `linkedins`,
`githubs`) are `text[]` columns with GIN indexes, because the matcher's
question is "who holds this one value in that array" — which is exactly what
`@>` over a GIN index answers.

Two conventions worth knowing before you open a query window:

- **Columns are `snake_case`; the JSON the API speaks is `camelCase`.** The
  two `_MAP` tables at the top of `app/store.py` are the only place that
  translation lives.
- **Timestamps are `text` holding ISO-8601 strings, not `timestamptz`.** The
  form posts a value with no timezone, the server stamps one in UTC, and the
  app sorts and compares them as strings throughout. One representation end to
  end beats converting at every edge — but it does mean `ORDER BY` on a date
  column is a string sort, so do not expect date arithmetic to work in SQL
  without a cast.

Interviews carry the fields from the form: team, name, interviewer, date and
time, skill set, education, experience, salary expectation, reason for leaving,
notice period, position, note, three rounds of feedback, status, and the CV
(a `jsonb` column holding the stored file's name, size and URL).

---

## Deploying to the office server

Runs on the `resume-filter` VM at **192.168.1.7** — nginx on port 80 in front
of uvicorn on loopback, with PostgreSQL and the uploaded CVs on the same
machine, so a VM snapshot is a complete backup.

```bash
# tar rather than rsync: Git Bash on Windows ships tar but not rsync.
# On Linux or macOS either works.
tar czf - --exclude='./.venv' --exclude='./data' --exclude='./.git' \
          --exclude='__pycache__' --exclude='./.pytest_cache' --exclude='./.env' . \
  | ssh madu@192.168.1.7 "rm -rf /tmp/resume-filter-src && mkdir -p /tmp/resume-filter-src \
                          && tar xzf - -C /tmp/resume-filter-src"

ssh madu@192.168.1.7 "sudo bash /tmp/resume-filter-src/deploy/install.sh"
```

The same two commands upgrade an existing install: `deploy/install.sh` is
idempotent and keeps the database, the uploaded CVs and the generated
password, replacing only the code and its dependencies.

**There is no login, and it is HTTP.** Anyone who can reach the VM can read
every candidate record and download every CV. That was always true, but it
matters more on an always-on server than on somebody's desktop — keep it off
any port forward.

See [deploy/README.md](deploy/README.md) for what goes where, backups,
restoring, and what to check when it will not start.

---

## Telling you what happened

Every action that changes something raises a toast in the bottom-right
corner, next to where the work is rather than over it.

| | |
|---|---|
| Green | it worked — saved, updated, deleted |
| Red | nothing was saved, and why |
| Amber | it worked, but read this — "different person", a CV with no text |
| Blue | for information — form cleared, nothing changed, returning candidate |

Each one names what actually happened rather than saying "Success": which
candidate, which fields changed, whether deleting the last application also
removed the person. Errors stay on screen nearly twice as long as
confirmations, hovering pauses the countdown, and the bar along the bottom
shows how long is left so a toast that disappears never looks like a glitch.
At most four are shown at once.

Form validation is handled in the page rather than by the browser, so a
missing name reports itself the same way everything else does — a toast, the
offending field outlined, and the cursor moved into it. A malformed email is
refused for a specific reason: email is the strongest matching signal there
is, and a typo silently turns it off, so the person would not be recognised
when they apply again.

---

## Configuration

Everything lives in `.env` (see `.env.example`).

| Key | Default | Notes |
|---|---|---|
| `PG_HOST` / `PG_PORT` | `localhost` / `5432` | |
| `PG_DATABASE` | `interview_tracker` | created on first run |
| `PG_USER` / `PG_PASSWORD` | `postgres` / — | password required unless the server trusts the connection |
| `PG_SSLMODE` | `prefer` | `require` for a database on another machine |
| `PG_POOL_MAX` | `10` | connections held open |
| `DATABASE_URL` | — | a full DSN; set it and every `PG_*` above is ignored |
| `CV_STORAGE_DIR` | `data/cvs` | relative to the project folder |
| `MAX_CV_MB` | `15` | upload limit |
| `IDENTITY_AUTO_LINK_MIN_CONFIDENCE` | `0.85` | see the ladder above |
| `IDENTITY_DEFAULT_PHONE_REGION` | `BD` | assumed when no country code |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | `0.0.0.0` exposes it on the LAN |

`.env` holds the database password and is gitignored. Keep it that way.

---

## API

The UI is a client of this; nothing is hidden from it.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | is PostgreSQL reachable |
| `GET` | `/api/meta` | status list, threshold |
| `GET` | `/api/dashboard` | counts + recent interviews |
| `POST` | `/api/check` | duplicate check — **read-only**, safe to call on every keystroke |
| `POST` | `/api/cv/parse` | upload a CV → extracted fields + duplicate check |
| `GET` | `/api/cv/{name}` | download a stored CV |
| `POST` | `/api/interviews` | record an interview |
| `DELETE` | `/api/interviews/{id}?candidateId=…` | remove one |
| `GET` | `/api/candidates?search=&status=` | list / search people, optionally by latest status; includes per-status counts |
| `GET` | `/api/candidates/{id}` | one person + full history |

Interactive docs at `/docs`.

`POST /api/interviews` decides who the interview belongs to:

- `candidateId` present → filed against that person (you confirmed it)
- `candidateId: null` → forced to a new person (you said "different person")
- absent → the matcher decides, linking only at or above the threshold

---

## Project layout

```
app/
  config.py      settings from .env
  identity.py    normalization — pure functions, doctested
  cv_parser.py   PyMuPDF / python-docx / optional OCR + field regexes
  store.py       PostgreSQL: lazy pool, schema created on first use
  service.py     the matching ladder and the write path
  main.py        FastAPI routes; also serves web/
web/
  index.html  styles.css  app.js     no build step, no Node
tests/
  test_identity.py  normalization: providers, URL shapes, phone formats
  test_cv_parser.py contact details out of CV text
  test_matching.py  the matching ladder and the write path
  test_api.py       the HTTP surface, as web/app.js drives it
run.py           python run.py [--reload]
start.ps1        venv + install + run
seed_demo.py     sample data, and --clear to undo
```

`identity.py` and the CV field extraction are adapted from the AINVIO
`Rag_azure` project (`app/utils/identity_normalize.py` and
`app/integrations/document_parser/regex_extractor.py`), with GitHub matching
added. Nothing else was carried over — no auth, no LLM, no search index.

### Tests

```powershell
.\.venv\Scripts\python -m pytest
```

264 tests. They run against a **real PostgreSQL server** — the same one in
`.env`, but a scratch database called `<PG_DATABASE>_test` that the suite
creates and truncates itself. Nothing can touch real data, and there is no
mock to drift out of step with how psycopg actually behaves.

| File | Covers |
|---|---|
| `tests/test_identity.py` | normalization — every provider, every URL shape, every phone format |
| `tests/test_cv_parser.py` | pulling contact details out of CV text |
| `tests/test_matching.py` | the matching ladder and the write path |
| `tests/test_api.py` | the HTTP surface, driven the way `web/app.js` drives it |

The normalization functions also carry doctests:

```powershell
.\.venv\Scripts\python -m doctest app\identity.py -v
```

Most of these tests exist because they caught something. The ones worth
knowing about:

- **`candidateId: null` used to be ignored.** The UI's "Different person"
  button sends it, but the API could not tell an explicit null from a missing
  key, so the matcher ran anyway and filed the second person's interview in
  the first person's history, under their name. A human who has looked at both
  records now wins, whatever the ladder thinks.
- **`normalize_github` matched `github.com` as a substring.** That accepted
  `api.github.com/users/ayesha` as the handle `users`, and
  `docs.github.com/en/…` as `en` — so every candidate carrying such a link
  merged into one fictional person, at 0.95 confidence, without asking. It
  also accepted `evil-github.com/ayesha`. The host is now checked exactly.
- **`normalize_linkedin` had the same flaw**, accepting any host's `/in/`
  path, so `xing.com/in/ayesha` was the LinkedIn user `ayesha`.
- **A wrapped-URL fix was swallowing the next line.** CVs stack their contact
  links, so the line after a LinkedIn URL is usually the GitHub one — and
  `https` is slug-shaped right up to the `:` that follows it. The slug came
  out as `…-98765https` and matched nobody.
