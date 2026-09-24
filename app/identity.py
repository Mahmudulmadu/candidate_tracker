"""Pure normalization for candidate identity matching.

Adapted from the AINVIO Rag_azure project's ``app/utils/identity_normalize.py``,
with GitHub added alongside LinkedIn.

Everything compared by the matcher is a *normalized* value, never a raw one:
two CVs written by the same person spell their email, phone and name
differently, and the job of this module is to make those spellings converge
before anything is compared.

No I/O and no settings import — every function is a pure transformation of a
string, which is what makes the matching ladder exhaustively testable.

A note on aggressiveness. Name normalization is the loosest function here on
purpose: a name-only match scores 0.40, *below* the auto-link threshold, so an
over-eager name match surfaces a "possible match" for a human to look at and
never silently declares two people to be one. Email, phone and profile
normalization are the opposite — they feed rungs that link automatically, so
they stay conservative.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import unquote

# ── Email ────────────────────────────────────────────────────────────
# Providers that ignore dots in the local part, so first.last@ and firstlast@
# are the same mailbox. This list stays SHORT by design: stripping dots on a
# domain that treats them as significant would merge two different people, and
# most providers (Outlook, Yahoo, every corporate domain) do treat them so.
_DOT_INSENSITIVE_DOMAINS = frozenset({"gmail.com", "googlemail.com"})
_DOMAIN_ALIASES = {"googlemail.com": "gmail.com"}
_EMAIL_STRIP = " \t\r\n<>()[]{}\"',;:|"


def normalize_email(raw: str | None) -> str:
    """Canonicalize an email address to a stable identity key.

    Lowercased, plus-tags removed, dots removed in the local part for Gmail
    only. Returns "" for anything that is not a single parseable address — an
    unparseable value must never become a match key, and "" makes the caller
    fall through to the next rung.

    >>> normalize_email("Ayesha.Rahman+jobs@GoogleMail.com")
    'ayesharahman@gmail.com'
    """
    if not raw:
        return ""

    value = str(raw).strip().strip(_EMAIL_STRIP)
    if value.lower().startswith("mailto:"):
        value = value[len("mailto:") :]
    value = value.strip().strip(_EMAIL_STRIP).lower()
    # PDF text extraction often leaves a trailing period or comma glued on.
    value = value.rstrip(".,;:")

    if value.count("@") != 1:
        return ""
    local, _, domain = value.partition("@")
    if not local or not domain or "." not in domain:
        return ""
    if " " in value or ".." in domain:
        return ""

    domain = _DOMAIN_ALIASES.get(domain, domain)
    # Plus-tagging is near-universal and "+" is vanishingly rare as a literal
    # local-part character, so this applies to every domain.
    local = local.split("+", 1)[0]
    if domain in _DOT_INSENSITIVE_DOMAINS:
        local = local.replace(".", "")
    if not local:
        return ""
    return f"{local}@{domain}"


# ── Phone ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Region:
    cc: str  # country calling code, no "+"
    trunk: str  # national trunk prefix, stripped when dialling internationally
    nsn_lengths: tuple[int, ...]  # valid national significant number lengths


# Bangladesh first because it is the primary market; the rest cover where
# candidates commonly dial in from.
_REGIONS: dict[str, _Region] = {
    "BD": _Region(cc="880", trunk="0", nsn_lengths=(10,)),
    "IN": _Region(cc="91", trunk="0", nsn_lengths=(10,)),
    "PK": _Region(cc="92", trunk="0", nsn_lengths=(10,)),
    "LK": _Region(cc="94", trunk="0", nsn_lengths=(9,)),
    "NP": _Region(cc="977", trunk="0", nsn_lengths=(10,)),
    "MY": _Region(cc="60", trunk="0", nsn_lengths=(9, 10)),
    "SG": _Region(cc="65", trunk="", nsn_lengths=(8,)),
    "AE": _Region(cc="971", trunk="0", nsn_lengths=(9,)),
    "SA": _Region(cc="966", trunk="0", nsn_lengths=(9,)),
    "QA": _Region(cc="974", trunk="", nsn_lengths=(8,)),
    "GB": _Region(cc="44", trunk="0", nsn_lengths=(9, 10)),
    "AU": _Region(cc="61", trunk="0", nsn_lengths=(9,)),
    "US": _Region(cc="1", trunk="1", nsn_lengths=(10,)),
    "CA": _Region(cc="1", trunk="1", nsn_lengths=(10,)),
}

DEFAULT_PHONE_REGION = "BD"

# E.164 allows at most 15 digits; below 7 it is an extension, not a number.
_MIN_E164_DIGITS = 7
_MAX_E164_DIGITS = 15

# How many trailing digits the ladder compares. Shorter than any full national
# number, so a number written with and without its country code still matches.
PHONE_MATCH_DIGITS = 9

# Country codes longest-first, so "880" is tested before "88" would be.
_CC_LOOKUP: tuple[tuple[str, _Region], ...] = tuple(
    sorted(
        {r.cc: r for r in reversed(list(_REGIONS.values()))}.items(),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
)


def _strip_embedded_trunk(digits: str) -> str:
    """Drop a national trunk prefix left inside an international number.

    CVs are full of "+880 (0) 1711223344" — the (0) is the trunk digit you drop
    when dialling in from abroad, and leaving it in produces a number one digit
    too long that matches nothing.
    """
    for cc, region in _CC_LOOKUP:
        if not digits.startswith(cc):
            continue
        rest = digits[len(cc) :]
        if len(rest) in region.nsn_lengths:
            return digits  # already well-formed
        if region.trunk and rest.startswith(region.trunk):
            trimmed = rest[len(region.trunk) :]
            if len(trimmed) in region.nsn_lengths:
                return cc + trimmed
        return digits
    return digits


def _best_effort_e164(digits: str) -> str:
    digits = _strip_embedded_trunk(digits.lstrip("0"))
    if _MIN_E164_DIGITS <= len(digits) <= _MAX_E164_DIGITS:
        return f"+{digits}"
    return ""


# CVs routinely list two numbers in one field — "01768438600,01992460122" or
# "017... / 019...". The whole string parses as nothing, so split and take the
# first part that works.
_PHONE_SEPARATORS = re.compile(r"\s*(?:[,;|/]|\bor\b|\band\b)\s*", re.IGNORECASE)


def normalize_phone(raw: str | None, default_region: str = DEFAULT_PHONE_REGION) -> str:
    """Normalize a phone number to E.164, assuming ``default_region`` when it
    carries no country code. Returns "" when the input cannot be a phone at all.

    >>> normalize_phone("01711-223344")
    '+8801711223344'
    >>> normalize_phone("+880 1711 223344")
    '+8801711223344'
    """
    if not raw:
        return ""

    text = str(raw).strip()
    # An extension ("... ext. 42") is not part of the identity.
    text = re.split(r"(?i)\b(?:ext|x|extn)\b\.?\s*\d+$", text)[0]

    whole = _normalize_one_phone(text, default_region)
    if whole:
        return whole
    # Not one number. Take the first part that parses: a candidate's primary
    # number is the one they write first.
    parts = [part.strip() for part in _PHONE_SEPARATORS.split(text) if part.strip()]
    if len(parts) > 1:
        for part in parts:
            candidate = _normalize_one_phone(part, default_region)
            if candidate:
                return candidate
    return ""


def _normalize_one_phone(text: str, default_region: str) -> str:
    """Normalize exactly one number; "" when the string is not a single number."""
    is_international = text.lstrip().startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    if not is_international and digits.startswith("00"):
        # 00 is the international access prefix in most of the world.
        is_international = True
        digits = digits[2:]

    if is_international:
        return _best_effort_e164(digits)

    region = _REGIONS.get((default_region or "").upper(), _REGIONS[DEFAULT_PHONE_REGION])

    # Already carries its own country code, just without the "+".
    if digits.startswith(region.cc):
        rest = digits[len(region.cc) :]
        if len(rest) in region.nsn_lengths:
            return f"+{digits}"

    # National form with the trunk prefix: 01711223344 -> +8801711223344.
    if region.trunk and digits.startswith(region.trunk):
        rest = digits[len(region.trunk) :]
        if len(rest) in region.nsn_lengths:
            return f"+{region.cc}{rest}"

    # Bare national significant number: 1711223344 -> +8801711223344.
    if len(digits) in region.nsn_lengths:
        return f"+{region.cc}{digits}"

    return _best_effort_e164(digits)


def phone_match_key(raw: str | None, default_region: str = DEFAULT_PHONE_REGION) -> str:
    """What the phone rung actually compares: the last 9 digits.

    Comparing tails rather than whole numbers lets "01711223344" on one CV
    match "+8801711223344" on another without this module having to be right
    about every country's dialling plan.

    Returns "" when there are fewer than 9 digits to compare — a partial number
    must not match everything ending the same way — and "" whenever
    ``normalize_phone`` refused the input. That second rule matters: this key
    feeds an auto-link rung, so deriving one from a string we declined to store
    would link people on a number that was never good enough to keep.
    """
    normalized = normalize_phone(raw, default_region)
    if not normalized:
        return ""
    digits = re.sub(r"\D", "", normalized)
    if len(digits) < PHONE_MATCH_DIGITS:
        return ""
    return digits[-PHONE_MATCH_DIGITS:]


# ── Name ─────────────────────────────────────────────────────────────
_HONORIFICS = frozenset(
    {
        "mr", "mrs", "ms", "miss", "mx", "sir", "madam",
        "dr", "doctor", "prof", "professor",
        "engr", "engineer", "eng", "er", "arch",
        "adv", "advocate", "hon", "honorable",
        "sri", "smt", "late",
    }
)

# Extremely common given-name prefixes in Bangladesh. Dropped only when a real
# name remains, so someone recorded simply as "Mohammad" keeps their name.
_GIVEN_NAME_PREFIXES = frozenset(
    {"md", "mohammad", "muhammad", "mohammed", "muhammed", "mohd", "moh"}
)

_SUFFIXES = frozenset(
    {
        "jr", "sr", "ii", "iii", "iv",
        "phd", "mphil", "mba", "bba", "bsc", "msc", "bs", "ms", "ba", "ma", "bcom", "mcom",
        "cse", "eee", "ece", "cs", "it",
        "pmp", "cfa", "acca", "cpa", "fcca", "cma", "ccna", "ccnp", "mcse",
    }
)


def _fold(text: str) -> str:
    """Strip accents and compatibility forms: "José" -> "Jose".

    Non-Latin scripts are folded but NOT transliterated, so a Bengali-script
    name will not match its Latin spelling. That is the safe direction: a
    missed match becomes a new candidate, a false match merges two people.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(raw: str | None) -> str:
    """Normalize a person's name into a comparable key.

    Folded, lowercased, punctuation removed, honorifics and qualifications
    dropped, initials discarded, then **token-sorted** — so "Rahman, Ayesha"
    and "Ayesha Rahman" produce the same key.

    >>> normalize_name("Md. Ayesha Rahman, PhD")
    'ayesha rahman'
    >>> normalize_name("Rahman, Ayesha")
    'ayesha rahman'
    """
    if not raw:
        return ""

    folded = _fold(str(raw)).lower()
    # Any non-alphanumeric becomes a separator, which also splits hyphenated
    # names into parts so "Jean-Luc" and "Jean Luc" agree.
    tokens = [t for t in re.split(r"[^a-z0-9]+", folded) if t]

    while tokens and tokens[0] in _HONORIFICS:
        tokens.pop(0)
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    while len(tokens) > 2 and tokens[0] in _GIVEN_NAME_PREFIXES:
        tokens.pop(0)
    if len(tokens) == 2 and tokens[0] in _GIVEN_NAME_PREFIXES:
        tokens.pop(0)

    # Middle initials are noise: "Ayesha K. Rahman" must match "Ayesha Rahman".
    # A token with no letter at all is not part of a name either — a field
    # filled in with "123" or a stray year must not become a match key that
    # two unrelated records could share.
    tokens = [t for t in tokens if len(t) > 1 and any(ch.isalpha() for ch in t)]
    if not tokens:
        return ""
    return " ".join(sorted(tokens))


# ── Profile URLs ─────────────────────────────────────────────────────
# The leading slash is optional so a hand-typed "in/ayesha-rahman" — the form
# LinkedIn itself displays — resolves the same as a full URL.
_LINKEDIN_PATH_RE = re.compile(r"(?:^|/)(?:in|pub|profile)/([^/?#\s]+)")
_LINKEDIN_NOISE = frozenset({"en", "bn", "en-us", "details", "recent-activity"})

# linkedin.com and its country subdomains (bd., uk., …) — and nothing else.
# Any other host's /in/ path is a different site's idea of a profile, and
# xing.com/in/ayesha is not the LinkedIn user "ayesha".
_LINKEDIN_HOST_RE = re.compile(r"^(?:[a-z0-9-]+\.)*linkedin\.com$")


def normalize_linkedin(raw: str | None) -> str:
    """Reduce a LinkedIn profile URL to its stable slug.

    Every spelling of the same profile — with or without scheme, www, a country
    subdomain, a trailing locale segment or tracking parameters — collapses to
    one value. Returns "" for anything that is not a personal profile, company
    pages included.

    >>> normalize_linkedin("https://bd.linkedin.com/in/Ayesha-Rahman-12345/en?trk=x")
    'ayesha-rahman-12345'
    """
    if not raw:
        return ""

    value = unquote(str(raw).strip()).lower()
    value = _SCHEME_RE.sub("", value)
    value = value.split("?")[0].split("#")[0].rstrip("/")
    if not value:
        return ""

    head, sep, rest = value.partition("/")
    if "." in head:
        # It carries a host, so the host has to be LinkedIn's, and the path
        # has to be a profile path — /feed and /company are not people.
        if not _LINKEDIN_HOST_RE.match(head):
            return ""
        match = _LINKEDIN_PATH_RE.search(f"/{rest}")
        if not match:
            return ""
        slug = match.group(1)
    else:
        # No host: either "in/ayesha-rahman", or a bare slug already extracted
        # by whoever filled the form.
        match = _LINKEDIN_PATH_RE.search(f"/{value}")
        if match:
            slug = match.group(1)
        elif sep:
            return ""  # some other path shape
        else:
            slug = value.lstrip("@")

    slug = slug.strip("/")
    if not slug or slug in _LINKEDIN_NOISE:
        return ""
    return slug


# GitHub reserves these paths, so none of them is a username. Without this a CV
# linking to github.com/orgs/... or a gist would produce a "username" that
# several unrelated people share — and profile matches link automatically.
_GITHUB_RESERVED = frozenset(
    {
        "about", "apps", "blog", "collections", "contact", "customer-stories",
        "dashboard", "enterprise", "events", "explore", "features", "gist",
        "home", "issues", "join", "login", "logout", "marketplace", "new",
        "notifications", "orgs", "organizations", "pricing", "pulls", "readme",
        "search", "security", "sessions", "settings", "shop", "showcases",
        "signup", "site", "sponsors", "stars", "team", "topics", "trending",
        "watching", "wiki",
    }
)
_GITHUB_USER_RE = re.compile(r"^[a-z\d](?:[a-z\d]|-(?=[a-z\d])){0,38}$")

# Only these hosts carry a profile. Matching "github.com" as a SUBSTRING
# instead would accept three kinds of URL that are not one:
#   api.github.com/users/ayesha   -> "users"   (every API link becomes one person)
#   docs.github.com/en/…          -> "en"      (every docs link becomes one person)
#   evil-github.com/ayesha        -> "ayesha"  (another site entirely)
# All of them feed a rung that links records automatically, so the host is
# checked exactly. gist.github.com is excluded too: its path can be a bare
# gist hash, which is not a person.
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})

# Scheme, precisely — so "github.com//x" is not mistaken for one.
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")


def normalize_github(raw: str | None) -> str:
    """Reduce a GitHub reference to its username.

    Accepts a full URL, a bare "@handle", or a plain username, and ignores any
    repository path after it — "github.com/ayesha-rahman/cv" is still the
    person "ayesha-rahman".

    Returns "" for anything that is not a *personal* handle: reserved paths,
    and strings that break GitHub's own username rules (alphanumeric and single
    inner hyphens, 39 characters max). Both exclusions matter because a profile
    match links two records automatically.

    >>> normalize_github("https://www.github.com/Ayesha-Rahman/my-cv")
    'ayesha-rahman'
    >>> normalize_github("@ayesha")
    'ayesha'
    """
    if not raw:
        return ""

    value = unquote(str(raw).strip()).lower()
    value = _SCHEME_RE.sub("", value)
    value = value.split("?")[0].split("#")[0].strip("/")
    if not value:
        return ""

    head, _, rest = value.partition("/")
    if "." in head:
        # It carries a host, so the host has to be GitHub's own.
        if head not in _GITHUB_HOSTS:
            return ""
        segments = [s for s in rest.split("/") if s]
        if not segments:
            return ""
        handle = segments[0]
    elif "/" in value:
        # A path with no host ("orgs/ainvio") is not a bare handle.
        return ""
    else:
        handle = value.lstrip("@")

    if handle in _GITHUB_RESERVED or not _GITHUB_USER_RE.match(handle):
        return ""
    return handle


# ── Bundle ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class NormalizedIdentity:
    """Every identity key for one record, normalized together.

    The matcher walks the ladder over exactly these fields, in this order:
    email -> profile (LinkedIn / GitHub) -> phone -> name.
    """

    email: str = ""
    phone: str = ""
    phone_key: str = ""
    name: str = ""
    linkedin: str = ""
    github: str = ""

    @property
    def has_strong_signal(self) -> bool:
        """True when at least one auto-link-capable signal is present.

        A record carrying only a name can never auto-link, however confident
        the name match looks.
        """
        return bool(self.email or self.linkedin or self.github or self.phone_key)


def normalize_identity(
    *,
    email: str | None = None,
    phone: str | None = None,
    name: str | None = None,
    linkedin: str | None = None,
    github: str | None = None,
    default_region: str = DEFAULT_PHONE_REGION,
) -> NormalizedIdentity:
    """Normalize every identity field from one record in one call."""
    return NormalizedIdentity(
        email=normalize_email(email),
        phone=normalize_phone(phone, default_region),
        phone_key=phone_match_key(phone, default_region),
        name=normalize_name(name),
        linkedin=normalize_linkedin(linkedin),
        github=normalize_github(github),
    )
