"""FastAPI app: JSON API plus the single-page UI it serves.

One process serves both, which is what makes deployment a single command on
the office machine — no Node, no build step, no reverse proxy to configure.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import cv_parser, service, store
from app.config import ROOT_DIR, settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
)
logger = logging.getLogger("tracker")

WEB_DIR = ROOT_DIR / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Nothing to open: the connection pool builds itself on first use, so the
    # app still serves the page that explains an unreachable database.
    yield
    # Closing it is worth doing explicitly — the pool runs worker threads, and
    # Ctrl+C would otherwise wait on them.
    store.close()


app = FastAPI(
    title="Candidate Interview Tracker",
    description="Track interview history and spot returning applicants.",
    version="1.0.0",
    lifespan=lifespan,
)

# The office may open this from another machine on the LAN.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(store.StoreError)
async def _store_error(_request, exc: store.StoreError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _value_error(_request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ── API ──────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return store.health()


@app.get("/api/meta")
def meta():
    """Choices the form renders, served from one place so they cannot drift."""
    return {
        "statuses": list(service.STATUSES),
        "autoLinkMinConfidence": settings.auto_link_min_confidence,
        "phoneRegion": settings.default_phone_region,
    }


@app.get("/api/dashboard")
def dashboard():
    return service.dashboard()


@app.post("/api/check")
def check(payload: dict):
    """Is this person already in the database? Read-only."""
    return service.check(payload or {})


@app.post("/api/cv/parse")
async def parse_cv(file: UploadFile = File(...)):
    """Read a CV and return what it says, without saving anything.

    Two separate things come back: the contact fields to pre-fill the form,
    and a stored copy of the file so the record can link to the actual CV.
    """
    content = await file.read()
    limit = int(settings.max_cv_mb * 1024 * 1024)
    if len(content) > limit:
        raise HTTPException(413, f"That file is larger than {settings.max_cv_mb:g} MB.")
    if not content:
        raise HTTPException(400, "That file is empty.")

    try:
        parsed = cv_parser.parse_cv(file.filename or "", content)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("CV parsing failed for %s", file.filename)
        raise HTTPException(500, f"Could not read that CV: {exc}") from exc

    stored_name = _store_cv(file.filename or "cv", content)
    parsed["cv"] = {
        "fileName": file.filename,
        "storedName": stored_name,
        "sizeBytes": len(content),
        "url": f"/api/cv/{stored_name}",
    }
    # Run the duplicate check immediately: the whole point of uploading first
    # is finding out you have met this person before.
    parsed["check"] = service.check(parsed)
    return parsed


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _store_cv(original: str, content: bytes) -> str:
    """Write the CV to disk under a name that cannot escape the folder.

    The uploaded name reaches us from a browser and is never trusted as a
    path — only its extension and a sanitized stem survive, in front of a
    random prefix that also stops two "resume.pdf" uploads colliding.
    """
    suffix = Path(original).suffix.lower()[:10]
    stem = _SAFE_NAME.sub("_", Path(original).stem)[:60] or "cv"
    stored = f"{uuid.uuid4().hex[:12]}_{stem}{suffix}"
    (settings.cv_dir / stored).write_bytes(content)
    return stored


@app.get("/api/cv/{stored_name}")
def download_cv(stored_name: str):
    path = (settings.cv_dir / stored_name).resolve()
    # Belt and braces: even with a sanitized write path, a read path taken
    # from the URL gets checked against the folder it must stay inside.
    # is_relative_to, not a string prefix — a prefix test also accepts a
    # sibling folder whose name merely starts the same ("data/cvs_backup").
    if not path.is_relative_to(settings.cv_dir.resolve()) or not path.is_file():
        raise HTTPException(404, "That CV is not on file.")
    return FileResponse(path, filename=stored_name.split("_", 1)[-1])


@app.post("/api/interviews")
def create_interview(payload: dict):
    """Record one interview.

    ``candidateId`` in the body means the interviewer confirmed a match the UI
    offered; without it the matcher decides on its own.
    """
    body = dict(payload or {})
    # An explicit null is the interviewer having looked at a proposed match
    # and said "different person". A MISSING key is them never having been
    # asked. Collapsing the two would let the matcher overrule a human who
    # already answered the question — and file one person's interview in a
    # stranger's history.
    force_new = "candidateId" in body and not body.get("candidateId")
    candidate_id = body.pop("candidateId", None) or None
    cv = body.pop("cv", None) or None
    if not (body.get("name") or "").strip():
        raise HTTPException(400, "A candidate name is required.")
    return service.record_interview(
        body, candidate_id=candidate_id, force_new=force_new, cv=cv
    )


@app.get("/api/candidates")
def list_candidates(search: str = "", status: str = "", limit: int = 200):
    """List people, optionally narrowed by search text and latest status.

    ``statusCounts`` are the numbers on the filter chips: per status, within
    the current search but ignoring the status filter itself.
    """
    search, status = search.strip(), status.strip()
    rows = store.list_candidates(search, limit=min(max(limit, 1), 500), status=status)
    return {
        "candidates": [service.public_candidate(r) for r in rows],
        "statusCounts": store.count_candidates_by_status(search),
    }


@app.get("/api/candidates/{candidate_id}")
def candidate_detail(candidate_id: str):
    profile = service.candidate_profile(candidate_id)
    if profile is None:
        raise HTTPException(404, "No such candidate.")
    return profile


@app.patch("/api/interviews/{interview_id}")
def patch_interview(interview_id: str, payload: dict):
    """Update one interview — how the 2nd and 3rd rounds get recorded.

    Send ONLY the fields that changed, plus ``candidateId``. A partial update
    means two interviewers editing different fields of the same record both
    keep their work; posting the whole form back would let whoever saved
    second silently revert the other.
    """
    body = dict(payload or {})
    candidate_id = (body.pop("candidateId", None) or "").strip()
    if not candidate_id:
        raise HTTPException(400, "candidateId is required.")
    return service.update_interview(interview_id, candidate_id, body)


@app.delete("/api/interviews/{interview_id}")
def delete_interview(interview_id: str, candidateId: str):
    """Remove one interview, and the candidate too if it was their last."""
    return service.remove_interview(interview_id, candidateId)


# ── UI ───────────────────────────────────────────────────────────────
# Mounted last so it never shadows an /api route.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:  # pragma: no cover - only when the web folder is missing
    @app.get("/")
    def _no_ui():
        return {"detail": "The web/ folder is missing; the API still works."}
