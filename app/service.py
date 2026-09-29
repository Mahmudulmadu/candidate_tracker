"""Identity matching and the write path.

THE LADDER
Given the contact details on a new application, find the person who already
exists in the database. Each rung is tried in order and carries a confidence:

    1.00  email            a mailbox is one person
    0.95  LinkedIn slug    a profile is one person
    0.95  GitHub handle    ditto
    0.85  phone            but ONLY when exactly one candidate holds that
                           exact number — shared desk phones and family
                           numbers are real
    0.60  phone tail       same last 9 digits, different full number: a
                           foreign number written once with its country code
                           and once without — or two people in two countries
    0.50  shared phone     the same number on several candidates
    0.40  name             never links on its own; surfaced for a human

Above ``auto_link_min_confidence`` the new interview is filed against the
existing candidate automatically. Below it, the UI shows a "possible match"
and lets the interviewer decide. A name match alone can never cross the line,
which is the whole point: the cost of wrongly splitting one person into two
records is a duplicate; the cost of wrongly merging two people is an interview
appearing in a stranger's history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app import store
from app.config import settings
from app.identity import NormalizedIdentity, normalize_identity, phone_match_key

# ── Confidence per rung ──────────────────────────────────────────────
CONF_EMAIL = 1.00
CONF_PROFILE = 0.95
CONF_PHONE = 0.85
CONF_PHONE_TAIL = 0.60
CONF_PHONE_SHARED = 0.50
CONF_NAME = 0.40


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Match:
    candidate: dict[str, Any]
    confidence: float
    signal: str  # "email" | "linkedin" | "github" | "phone" | "name"
    detail: str  # what a person reads: "same email — ayesha@gmail.com"

    @property
    def is_certain(self) -> bool:
        return self.confidence >= settings.auto_link_min_confidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidate.get("id"),
            "candidate": public_candidate(self.candidate),
            "confidence": round(self.confidence, 2),
            "signal": self.signal,
            "detail": self.detail,
            "certain": self.is_certain,
        }


def build_identity(payload: dict[str, Any]) -> NormalizedIdentity:
    return normalize_identity(
        email=payload.get("email"),
        phone=payload.get("phone"),
        name=payload.get("name"),
        linkedin=payload.get("linkedin"),
        github=payload.get("github"),
        default_region=settings.default_phone_region,
    )


def find_matches(identity: NormalizedIdentity) -> list[Match]:
    """Every candidate this identity could be, best rung first.

    Returns at most one entry per candidate — the strongest signal that found
    them. Somebody matching on both email and phone is one match at 1.00, not
    two rows saying the same thing.
    """
    found: dict[str, Match] = {}

    def offer(rows: list[dict], confidence: float, signal: str, detail: str) -> None:
        for row in rows:
            cid = row.get("id")
            if not cid:
                continue
            existing = found.get(cid)
            if existing is None or confidence > existing.confidence:
                found[cid] = Match(row, confidence, signal, detail)

    if identity.email:
        offer(store.find_by_email(identity.email), CONF_EMAIL, "email",
              f"Same email — {identity.email}")

    if identity.linkedin:
        offer(store.find_by_linkedin(identity.linkedin), CONF_PROFILE, "linkedin",
              f"Same LinkedIn profile — /in/{identity.linkedin}")

    if identity.github:
        offer(store.find_by_github(identity.github), CONF_PROFILE, "github",
              f"Same GitHub account — @{identity.github}")

    if identity.phone_key:
        # The key is the last 9 digits. That is what FINDS a number however
        # it was written — +88, +880, 0, nothing — and for a Bangladeshi
        # mobile it loses nothing, since every one starts 01. Whether a find
        # is good enough to link on its own is then decided on the WHOLE
        # number: a 9-digit tail is shared by numbers in different countries,
        # and +91 97112 23344 is not the same person as 01711-223344.
        rows = store.find_by_phone_key(identity.phone_key)
        same_number = [r for r in rows if identity.phone in (r.get("phones") or [])]
        exact_ids = {r.get("id") for r in same_number}

        if len(same_number) == 1:
            offer(same_number, CONF_PHONE, "phone",
                  f"Same phone number — {identity.phone}")
        elif same_number:
            # A number held by two candidates is a shared line, not proof of
            # identity. Still reported — below the auto-link bar, so a person
            # decides.
            offer(same_number, CONF_PHONE_SHARED, "phone",
                  f"Phone {identity.phone} is on {len(same_number)} candidate "
                  "records (shared line?)")

        for row in rows:
            if row.get("id") in exact_ids:
                continue
            theirs = next((p for p in row.get("phones") or []
                           if phone_match_key(p) == identity.phone_key), "")
            offer([row], CONF_PHONE_TAIL, "phone",
                  f"Phone {identity.phone} ends in the same 9 digits as "
                  f"{theirs or 'a number on file'}, but the country code differs "
                  "— check it is the same number")

    if identity.name:
        offer(store.find_by_name_key(identity.name), CONF_NAME, "name",
              "Same name — but no matching email, phone or profile")

    return sorted(found.values(), key=lambda m: m.confidence, reverse=True)


def check(payload: dict[str, Any]) -> dict[str, Any]:
    """The duplicate check the form runs as the interviewer types.

    Read-only: nothing here writes, so it is safe to call on every keystroke.
    """
    identity = build_identity(payload)
    matches = find_matches(identity)
    best = matches[0] if matches else None

    history: list[dict[str, Any]] = []
    if best is not None:
        history = [public_interview(i) for i in store.list_interviews_for(best.candidate["id"])]

    return {
        "normalized": {
            "email": identity.email,
            "phone": identity.phone,
            "name": identity.name,
            "linkedin": identity.linkedin,
            "github": identity.github,
        },
        "hasStrongSignal": identity.has_strong_signal,
        "isReturning": bool(best and best.is_certain),
        "matches": [m.to_dict() for m in matches],
        "bestMatch": best.to_dict() if best else None,
        "history": history,
    }


# ── Writing ──────────────────────────────────────────────────────────
# Fields copied verbatim from the form onto the interview document.
INTERVIEW_FIELDS = (
    "team", "name", "interviewer", "interviewDateTime", "skillSet", "education",
    "experience", "salaryExpectation", "reasonForLeaving", "noticePeriod",
    "position", "note", "feedback1", "feedback2", "feedback3", "status",
    "email", "phone", "linkedin", "github",
)

STATUSES = (
    "Applied", "Interviewing", "On Hold", "Selected", "Rejected", "Offer Sent",
    "Joined", "Declined", "No Show", "NSOC", "Internal",
)


def record_interview(
    payload: dict[str, Any],
    *,
    candidate_id: str | None = None,
    force_new: bool = False,
    cv: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Save one interview, attaching it to the right person.

    Three ways in, and they are genuinely different:

    ``candidate_id``  the interviewer answered "same person" — link to them.
    ``force_new``     the interviewer answered "different person" — start a
                      new record, and do NOT let the matcher overrule them.
                      A human who has looked at both records knows something
                      the ladder does not; the whole point of asking is that
                      the answer counts.
    neither           nobody was asked, so the matcher decides: a match at or
                      above the threshold links, anything weaker starts a new
                      candidate record.
    """
    identity = build_identity(payload)

    if candidate_id:
        candidate = store.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError("That candidate no longer exists. Reload and try again.")
        link_reason = "confirmed by interviewer"
    elif force_new:
        candidate = _new_candidate(payload, identity)
        store.save_candidate(candidate)
        link_reason = "new candidate — interviewer said this is someone else"
    else:
        matches = find_matches(identity)
        best = matches[0] if matches else None
        if best is not None and best.is_certain:
            candidate = best.candidate
            link_reason = best.detail
        else:
            candidate = _new_candidate(payload, identity)
            # Persist the shell before the interview: interviews carry a
            # foreign key to candidates, so the person has to exist first.
            # It is written again below, once the interview has been folded in.
            store.save_candidate(candidate)
            link_reason = "new candidate"

    interview = {
        "id": f"int_{uuid.uuid4().hex[:16]}",
        "candidateId": candidate["id"],
        "createdAt": utc_now_iso(),
        "updatedAt": utc_now_iso(),
        "linkReason": link_reason,
        "cv": cv or None,
    }
    for field in INTERVIEW_FIELDS:
        interview[field] = (payload.get(field) or "").strip()
    if not interview["interviewDateTime"]:
        interview["interviewDateTime"] = utc_now_iso()

    store.save_interview(interview)

    candidate = _absorb(candidate, payload, identity, interview)
    store.save_candidate(candidate)

    return {
        "interview": public_interview(interview),
        "candidate": public_candidate(candidate),
        "linkedTo": candidate["id"],
        "linkReason": link_reason,
    }


def update_interview(
    interview_id: str,
    candidate_id: str,
    changes: dict[str, Any],
) -> dict[str, Any]:
    """Apply a partial update to one interview — the later rounds.

    This is how a 2nd or 3rd round gets recorded. The first round creates the
    interview; every round after it EDITS that same record, so one application
    stays one row in the candidate's history however many times the panel
    meets them.

    Only the keys actually present in ``changes`` are written. That matters
    with two interviewers working at once: one adding round-2 feedback and
    another moving the status along are editing different fields, and a
    partial update lets both survive. Sending the whole form back would mean
    whoever saved second silently reverted the other's change.
    """
    interview = store.get_interview(interview_id, candidate_id)
    if interview is None:
        raise ValueError("That interview no longer exists. Reload and try again.")

    touched = False
    for field in INTERVIEW_FIELDS:
        if field not in changes:
            continue
        value = (changes.get(field) or "").strip()
        if interview.get(field, "") != value:
            interview[field] = value
            touched = True

    if not touched:
        return {
            "interview": public_interview(interview),
            "candidate": public_candidate(store.get_candidate(candidate_id) or {}),
            "changed": False,
        }

    interview["updatedAt"] = utc_now_iso()
    if not interview.get("interviewDateTime"):
        interview["interviewDateTime"] = interview.get("createdAt") or utc_now_iso()
    store.save_interview(interview)

    candidate = store.get_candidate(candidate_id)
    if candidate is not None:
        # An edit can change an email or a phone — the candidate must keep
        # matching on the corrected value as well as the original one.
        _add_identity_keys(candidate, build_identity(interview))
        candidate = _resync(candidate)
        store.save_candidate(candidate)

    return {
        "interview": public_interview(interview),
        "candidate": public_candidate(candidate or {}),
        "changed": True,
    }


def remove_interview(interview_id: str, candidate_id: str) -> dict[str, Any]:
    """Delete one interview and put the candidate's summary back in step.

    When it was their only one the candidate goes too: a person with no
    interviews is not a record anybody wants in the list, and leaving the
    shell behind would keep matching future applicants against somebody with
    no history to show them.
    """
    interview = store.get_interview(interview_id, candidate_id)
    if interview is None:
        raise ValueError("That interview no longer exists. Reload and try again.")

    if not store.delete_interview(interview_id, candidate_id):
        raise store.StoreError("Could not delete that interview. Try again.")

    remaining = store.count_interviews_for(candidate_id)
    if remaining is None:
        # The count query failed. Leave the candidate exactly as it is rather
        # than guessing — a wrong guess here deletes a person's whole record.
        return {"deleted": interview_id, "candidateDeleted": False, "remaining": None}

    if remaining == 0:
        store.delete_candidate(candidate_id)
        return {"deleted": interview_id, "candidateDeleted": True, "remaining": 0}

    candidate = store.get_candidate(candidate_id)
    if candidate is not None:
        store.save_candidate(_resync(candidate))
    return {"deleted": interview_id, "candidateDeleted": False, "remaining": remaining}


def _resync(candidate: dict[str, Any]) -> dict[str, Any]:
    """Recompute a candidate's summary fields from their interviews.

    The create path keeps these up to date incrementally; an edit cannot,
    because changing a status or a date re-opens the question of which
    interview is the most recent one. Reading them back is cheap (a
    single-partition query) and is always right.
    """
    rows = store.list_interviews_for(candidate["id"])  # newest first
    if not rows:
        # Either this candidate genuinely has no interviews, or the read
        # failed — `_query` degrades to []. Zeroing the summary on a failed
        # read would quietly erase someone's history from every listing, so
        # leave the existing values alone and let the next edit fix them.
        return candidate

    dates = [r.get("interviewDateTime") or r.get("createdAt") or "" for r in rows]
    dates = [d for d in dates if d]
    latest = rows[0]

    candidate["interviewCount"] = len(rows)
    candidate["lastInterviewAt"] = max(dates) if dates else ""
    candidate["firstInterviewAt"] = min(dates) if dates else ""
    candidate["lastStatus"] = latest.get("status") or ""
    candidate["lastPosition"] = latest.get("position") or ""
    name = (latest.get("name") or "").strip()
    if name:
        candidate["displayName"] = name
    candidate["updatedAt"] = utc_now_iso()
    _rebuild_search_blob(candidate)
    return candidate


def _new_candidate(payload: dict[str, Any], identity: NormalizedIdentity) -> dict[str, Any]:
    return {
        "id": f"cand_{uuid.uuid4().hex[:16]}",
        "displayName": (payload.get("name") or "").strip() or "Unnamed candidate",
        "nameKey": identity.name,
        "emails": [],
        "phones": [],
        "phoneKeys": [],
        "linkedins": [],
        "githubs": [],
        "searchBlob": "",
        "interviewCount": 0,
        "createdAt": utc_now_iso(),
        "updatedAt": utc_now_iso(),
        "firstInterviewAt": "",
        "lastInterviewAt": "",
        "lastStatus": "",
        "lastPosition": "",
    }


def _absorb(
    candidate: dict[str, Any],
    payload: dict[str, Any],
    identity: NormalizedIdentity,
    interview: dict[str, Any],
) -> dict[str, Any]:
    """Fold a new interview's details into the candidate record.

    Identity keys ACCUMULATE rather than overwrite. Someone who applied with a
    university address in 2023 and a work address today is one person with two
    mailboxes, and forgetting the old one would make their own history stop
    matching them.
    """
    _add_identity_keys(candidate, identity)

    name = (payload.get("name") or "").strip()
    if name and candidate.get("displayName") in ("", "Unnamed candidate"):
        candidate["displayName"] = name
    if identity.name and not candidate.get("nameKey"):
        candidate["nameKey"] = identity.name

    when = interview.get("interviewDateTime") or interview["createdAt"]
    candidate["interviewCount"] = int(candidate.get("interviewCount") or 0) + 1
    candidate["lastInterviewAt"] = max(when, candidate.get("lastInterviewAt") or "")
    candidate["firstInterviewAt"] = min(
        when, candidate.get("firstInterviewAt") or when
    )
    candidate["lastStatus"] = interview.get("status") or candidate.get("lastStatus") or ""
    candidate["lastPosition"] = interview.get("position") or candidate.get("lastPosition") or ""
    candidate["updatedAt"] = utc_now_iso()
    _rebuild_search_blob(candidate)
    return candidate


def _add_identity_keys(candidate: dict[str, Any], identity: NormalizedIdentity) -> None:
    """Accumulate identity keys onto a candidate — never overwrite them.

    Someone who applied with a university address in 2023 and a work address
    today is one person with two mailboxes. Dropping the old one would make
    their own history stop matching them.
    """
    def add(key: str, value: str) -> None:
        if value and value not in candidate.setdefault(key, []):
            candidate[key].append(value)

    add("emails", identity.email)
    add("phones", identity.phone)
    add("phoneKeys", identity.phone_key)
    add("linkedins", identity.linkedin)
    add("githubs", identity.github)


def _rebuild_search_blob(candidate: dict[str, Any]) -> None:
    """What free-text search runs against.

    Rebuilt from scratch every write so a corrected spelling actually becomes
    findable, rather than the search index keeping the typo alive.
    """
    candidate["searchBlob"] = " ".join(
        filter(
            None,
            [
                candidate.get("displayName", ""),
                *candidate.get("emails", []),
                *candidate.get("phones", []),
                *candidate.get("linkedins", []),
                *candidate.get("githubs", []),
                candidate.get("lastPosition", ""),
            ],
        )
    )


# ── Read models ──────────────────────────────────────────────────────
def public_candidate(doc: dict[str, Any]) -> dict[str, Any]:
    """Drop any underscore-prefixed bookkeeping before it reaches the UI."""
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def public_interview(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def candidate_profile(candidate_id: str) -> dict[str, Any] | None:
    candidate = store.get_candidate(candidate_id)
    if candidate is None:
        return None
    return {
        "candidate": public_candidate(candidate),
        "history": [public_interview(i) for i in store.list_interviews_for(candidate_id)],
    }


def dashboard() -> dict[str, Any]:
    recent = store.list_recent_interviews(limit=8)
    return {
        "candidates": store.count_candidates(),
        "interviews": store.count_interviews(),
        "recent": [public_interview(i) for i in recent],
    }
