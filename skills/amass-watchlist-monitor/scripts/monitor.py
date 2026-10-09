#!/usr/bin/env python3
"""Amass watchlist monitor.

Keeps a named list of Amass records under watch and reports what changed since
the last run: the scoped change feed says which records moved, a fresh GET of
each one is diffed field by field against the snapshot stored last time.

    python3 monitor.py run <watchlist.yaml> [--dry-run] [--since YYYY-MM-DD]
                           [--state-dir PATH] [--max-credits N]

Standard library only, Python 3.11+. Reads the API key from AMASS_API_KEY.
Exit codes: 0 done, 1 failed (or finished with record errors), 2 stopped by
the run's safety limit (--max-credits) before making more calls.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import email.utils
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

VERSION = "0.1.0"
BASE_URL = "https://api.amass.tech/api/v1"

CHUNK_SIZE = 100  # the feed takes at most 100 amassId values per request
PAGE_LIMIT = 100  # a scoped page never holds more than 100 records
RESUME_OVERLAP_DAYS = 2  # each sweep starts this many days before the stored since
LOOKUP_BATCH = 100
MAX_RATE_LIMIT_RETRIES = 10
MAX_TRANSIENT_RETRIES = 3
HTTP_TIMEOUT = 60
DEFAULT_MAX_CREDITS = 500

# Digest classes, in the order the digest lists them.
REMOVED = "Removed from source"
STATUS = "Status and stage changes"
RESULTS = "Results posted or revised"
LABEL = "Label and SmPC section changes"
RETRACTIONS = "Retractions and corrections"
LINKS = "New cross-links"
OTHER = "Other field changes"
METADATA = "Metadata-only"
NEW = "New to Amass"
BASELINE = "Baseline captured"
CLASS_ORDER = (REMOVED, STATUS, RESULTS, LABEL, RETRACTIONS, LINKS, OTHER, METADATA, NEW, BASELINE)
SUBSTANTIVE = {STATUS, RESULTS, LABEL, RETRACTIONS, LINKS, OTHER}
DIGEST_CAPS = {BASELINE: 50, METADATA: 100}

# NLM publication types that mark a retraction, correction or concern notice.
CORRECTION_TYPES = {
    "Retracted Publication",
    "Retraction of Publication",
    "Published Erratum",
    "Corrected and Republished Article",
    "Retraction and Republication",
    "Expression of Concern",
}

MISSING = object()


class MonitorError(Exception):
    """A failure the caller has to see: bad watchlist, bad id, API error."""


class BudgetExceeded(MonitorError):
    """The run would spend more than --max-credits."""


class ApiError(MonitorError):
    def __init__(self, status: int, code: str, message: str, path: str):
        super().__init__(f"HTTP {status} {code} on {path}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.path = path


# --------------------------------------------------------------------------
# Per-Core rules
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Rule:
    field: str  # top-level field or dotted path
    kind: str  # scalar | presence | text | set | pubtypes | keyed | object
    cls: str
    key: Callable[[Any], Any] | None = None
    label: Callable[[Any], str] | None = None


@dataclasses.dataclass(frozen=True)
class Core:
    name: str
    label: str
    prefix: str
    include_default: tuple[str, ...]
    include_allowed: tuple[str, ...]
    lookup_keys: tuple[str, ...]
    rules: tuple[Rule, ...]
    title: Callable[[dict], str | None]
    ident: Callable[[dict], str | None]
    state: Callable[[dict], str]
    metadata_fields: tuple[str, ...] = ()  # diffed, but reported as Metadata-only

    def valid_id(self, value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(re.escape(self.prefix) + r"[0-9A-Za-z]{16,32}", value) is not None

    def path(self, *parts: str) -> str:
        return "/cores/" + self.name + "".join("/" + urllib.parse.quote(p, safe="") for p in parts)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _outcome_key(o: dict) -> tuple:
    return (_s(o.get("outcomeType")), _s(o.get("title")).strip().lower())


def _outcome_label(o: dict) -> str:
    return f"{o.get('outcomeType') or 'OUTCOME'} “{_trunc(o.get('title') or '(untitled)', 90)}”"


def _designation_key(d: dict) -> tuple:
    return (_s(d.get("agency")), _s(d.get("axis")), _s(d.get("type")))


def _designation_label(d: dict) -> str:
    return " ".join(x for x in (d.get("agency"), d.get("type"), d.get("nativeName") and f"({d['nativeName']})") if x)


def _moa_key(m: dict) -> tuple:
    targets = tuple(sorted(_s(t.get("ensemblId")) for t in (m.get("targets") or []) if isinstance(t, dict)))
    return (_s(m.get("actionType")), targets)


def _moa_label(m: dict) -> str:
    symbols = ", ".join(_s(t.get("symbol") or t.get("ensemblId")) for t in (m.get("targets") or []) if isinstance(t, dict))
    return f"{m.get('actionType') or 'UNKNOWN'} on {symbols or 'no target'}"


def _safety_key(s: dict) -> tuple:
    return (_s(s.get("event")).lower(), _s(s.get("datasource")))


def _safety_label(s: dict) -> str:
    return f"{s.get('event') or '(event)'} [{s.get('datasource') or 'source?'}]"


def _trial_ident(r: dict) -> str | None:
    return r.get("nctId") or r.get("registryId")


def _paper_ident(r: dict) -> str | None:
    if r.get("pmid"):
        return f"PMID {r['pmid']}"
    return f"DOI {r['doi']}" if r.get("doi") else None


def _authorization_ident(r: dict) -> str | None:
    number = (r.get("fdaDetails") or {}).get("applicationNumber") or (r.get("emaDetails") or {}).get("productNumber")
    return " ".join(x for x in (r.get("agency"), number) if x) or None


def _authorization_title(r: dict) -> str | None:
    name, substance = r.get("name"), r.get("activeSubstance")
    if name and substance and substance.lower() != name.lower():
        return f"{name} ({substance})"
    return name or substance


TRIALCORE = Core(
    name="trialcore",
    label="TrialCore",
    prefix="AMTC_",
    include_default=("outcomes", "referencesBiomedCore"),
    include_allowed=("outcomes", "referencesBiomedCore", "referencesDrugCore", "detailedDescription"),
    lookup_keys=("nctId", "registryId"),
    rules=(
        Rule("overallStatus", "scalar", STATUS),
        Rule("whyStopped", "scalar", STATUS),
        Rule("phase", "scalar", STATUS),
        Rule("enrollment", "scalar", STATUS),
        Rule("enrollmentType", "scalar", STATUS),
        Rule("startDate", "scalar", STATUS),
        Rule("completionDate", "scalar", STATUS),
        Rule("hasResults", "scalar", RESULTS),
        Rule("resultsFirstPostDate", "scalar", RESULTS),
        Rule("outcomes", "keyed", RESULTS, _outcome_key, _outcome_label),
        Rule("referencesBiomedCore", "set", LINKS),
        Rule("sponsorName", "scalar", OTHER),
        Rule("conditions", "set", OTHER),
        Rule("interventionNames", "set", OTHER),
        Rule("interventionTypes", "set", OTHER),
    ),
    title=lambda r: r.get("briefTitle") or r.get("officialTitle"),
    ident=_trial_ident,
    state=lambda r: " · ".join(
        x
        for x in (
            r.get("overallStatus"),
            r.get("phase"),
            "results posted" if r.get("hasResults") else "no results posted",
        )
        if x
    ),
)

BIOMEDCORE = Core(
    name="biomedcore",
    label="BiomedCore",
    prefix="AMBC_",
    include_default=("referencesTrialCore",),
    include_allowed=("referencesTrialCore", "authorsMetadata", "meshIds", "substanceIds", "references", "citedBy"),
    lookup_keys=("pmid", "doi"),
    rules=(
        Rule("isRetracted", "scalar", RETRACTIONS),
        Rule("publicationTypes", "pubtypes", OTHER),
        Rule("title", "scalar", OTHER),
        Rule("abstract", "presence", OTHER),
        Rule("hasFulltext", "scalar", OTHER),
        Rule("publicationDate", "scalar", OTHER),
        Rule("referencesTrialCore", "set", LINKS),
    ),
    title=lambda r: r.get("title"),
    ident=_paper_ident,
    state=lambda r: " · ".join(
        x for x in (r.get("journal"), r.get("publicationDate"), "RETRACTED" if r.get("isRetracted") else None) if x
    ),
    metadata_fields=("citationCount",),
)

REGULATORYCORE = Core(
    name="regulatorycore",
    label="RegulatoryCore",
    prefix="AMRC_",
    include_default=("fdaDetails", "emaDetails"),
    include_allowed=("fdaDetails", "emaDetails", "referencesDrugCore"),
    lookup_keys=("fdaApplicationNumber", "emaProductNumber", "ndc", "splSetId"),
    rules=(
        Rule("authorizationStatus", "scalar", STATUS),
        Rule("designations", "keyed", STATUS, _designation_key, _designation_label),
        Rule("isOrphan", "scalar", STATUS),
        Rule("fdaDetails.withdrawalDate", "scalar", STATUS),
        Rule("therapeuticIndication", "text", LABEL),
        Rule("fdaDetails.labelDate", "scalar", LABEL),
        Rule("emaDetails.smpcDate", "scalar", LABEL),
        Rule("emaDetails.revisionNumber", "scalar", LABEL),
        # documentSections (the table of contents) is diffed together with the
        # feed's section events, see section_lines().
    ),
    title=_authorization_title,
    ident=_authorization_ident,
    state=lambda r: " · ".join(
        x
        for x in (
            r.get("agency"),
            r.get("authorizationStatus"),
            f"{len(r.get('documentSections') or [])} document sections",
        )
        if x
    ),
)

DRUGCORE = Core(
    name="drugcore",
    label="DrugCore",
    prefix="AMDC_",
    include_default=("referencesTrialCore", "referencesRegulatoryCore"),
    include_allowed=(
        "referencesTrialCore",
        "referencesRegulatoryCore",
        "referencesBiomedCore",
        "referencesGeneCore",
        "parent",
        "children",
    ),
    lookup_keys=("chemblId",),
    rules=(
        Rule("maxClinicalStage", "scalar", STATUS),
        Rule("drugType", "scalar", STATUS),
        Rule("mechanismsOfAction", "keyed", OTHER, _moa_key, _moa_label),
        Rule("tradeNames", "set", OTHER),
        Rule("synonyms", "set", OTHER),
        Rule("referencesTrialCore", "set", LINKS),
        Rule("referencesRegulatoryCore", "set", LINKS),
    ),
    title=lambda r: r.get("name"),
    ident=lambda r: r.get("chemblId"),
    state=lambda r: " · ".join(x for x in (r.get("drugType"), r.get("maxClinicalStage")) if x),
)

GENECORE = Core(
    name="genecore",
    label="GeneCore",
    prefix="AMGC_",
    include_default=("referencesDrugCore",),
    include_allowed=("referencesDrugCore", "protein"),
    lookup_keys=("ensemblGeneId", "hgncId", "entrezGeneId", "uniprotId", "symbol", "omimId", "orphanet", "iuphar"),
    rules=(
        Rule("symbol", "scalar", OTHER),
        Rule("name", "scalar", OTHER),
        Rule("synonyms", "set", OTHER),
        Rule("tractability", "object", OTHER),
        Rule("safetyLiabilities", "keyed", OTHER, _safety_key, _safety_label),
        Rule("referencesDrugCore", "set", LINKS),
    ),
    title=lambda r: " · ".join(x for x in (r.get("symbol"), r.get("name")) if x) or None,
    ident=lambda r: r.get("ensemblGeneId"),
    state=lambda r: r.get("geneType") or "",
)

CORES = {c.name: c for c in (TRIALCORE, BIOMEDCORE, REGULATORYCORE, DRUGCORE, GENECORE)}
PREFIX_TO_CORE = {c.prefix: c.label for c in CORES.values()} | {"AMPC_": "PatentCore"}

CADENCE = {
    "trialcore": "daily",
    "biomedcore": "daily",
    "regulatorycore": "weekly",
    "drugcore": "weekly",
    "genecore": "weekly",
}


def next_refresh(core: Core, today: dt.date) -> str:
    if CADENCE[core.name] == "daily":
        return (
            f"{today + dt.timedelta(days=1)}. {core.label} refreshes daily; "
            "a day's changes reach the feed about one day later."
        )
    return (
        f"within a week. {core.label} refreshes weekly; "
        "changes reach the feed about one day after each refresh."
    )


# --------------------------------------------------------------------------
# Watchlist file
# --------------------------------------------------------------------------

WATCHLIST_KEYS = {"name", "core", "ids", "include", "cadence", "description"}


def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def _scalar(text: str) -> str | None:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    if text in ("", "~", "null"):
        return None
    return text


def _flow_list(text: str, where: str) -> list:
    inner = text.strip()[1:-1].strip()
    if not inner:
        return []
    if any(c in inner for c in "[]{}"):
        raise MonitorError(f"{where}: nested lists are not supported")
    return [_scalar(part) for part in inner.split(",")]


def parse_yaml_subset(text: str, source: str) -> dict:
    """Parse the small YAML subset a watchlist uses.

    Top-level `key: value`, `key: [a, b]`, and `key:` followed by `- item` or
    `- idType: value` lines. Anything else is an error, never a guess.
    """
    data: dict[str, Any] = {}
    list_key: str | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        where = f"{source}:{lineno}"
        line = _strip_comment(raw).rstrip()
        if not line.strip() or line.strip() in ("---", "..."):
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise MonitorError(f"{where}: use spaces, not tabs, for indentation")
        stripped = line.strip()
        if line[0] != " " and not stripped.startswith("-"):
            match = re.fullmatch(r"([A-Za-z_][\w-]*)\s*:(.*)", stripped)
            if not match:
                raise MonitorError(f"{where}: expected `key: value`, got {stripped!r}")
            key, rest = match.group(1), match.group(2).strip()
            if key in data:
                raise MonitorError(f"{where}: duplicate key {key!r}")
            if rest == "":
                data[key] = []
                list_key = key
            elif rest.startswith("["):
                if not rest.endswith("]"):
                    raise MonitorError(f"{where}: unterminated list")
                data[key] = _flow_list(rest, where)
                list_key = None
            else:
                data[key] = _scalar(rest)
                list_key = None
            continue
        if list_key is None or not stripped.startswith("-"):
            raise MonitorError(f"{where}: unexpected line {stripped!r}")
        item = stripped[1:].strip()
        mapping = re.fullmatch(r"([A-Za-z_]\w*)\s*:\s+(.+)", item)
        if mapping and item[0] not in "'\"":
            data[list_key].append({mapping.group(1): _scalar(mapping.group(2))})
        elif item.endswith(":"):
            raise MonitorError(f"{where}: an id entry needs a value, got {item!r}")
        else:
            data[list_key].append(_scalar(item))
    return data


def load_watchlist(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise MonitorError(f"cannot read watchlist {path}: {err.strerror}") from None
    if path.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise MonitorError(f"{path}: invalid JSON: {err}") from None
    else:
        data = parse_yaml_subset(text, path.name)
    if not isinstance(data, dict):
        raise MonitorError(f"{path}: a watchlist is a mapping with name, core and ids")
    unknown = sorted(set(data) - WATCHLIST_KEYS)
    if unknown:
        raise MonitorError(f"{path}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(WATCHLIST_KEYS))}")

    name = data.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        raise MonitorError(f"{path}: `name` must be letters, digits, '.', '_' or '-' (it names the state folder)")
    core_name = str(data.get("core") or "").strip().lower()
    if core_name == "patentcore":
        raise MonitorError(
            "PatentCore cannot be watched: it has no change feed and no Amass write dates, so there is "
            "nothing to sweep. Watch TrialCore, BiomedCore, RegulatoryCore, DrugCore or GeneCore."
        )
    if core_name not in CORES:
        raise MonitorError(f"{path}: `core` must be one of {', '.join(CORES)} (got {data.get('core')!r})")
    core = CORES[core_name]

    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        raise MonitorError(f"{path}: `ids` must be a non-empty list")
    entries: list[str | tuple[str, str]] = []
    for item in ids:
        if isinstance(item, str):
            entries.append(item.strip())
        elif isinstance(item, dict) and len(item) == 1:
            (key, value), = item.items()
            if key not in core.lookup_keys:
                raise MonitorError(
                    f"{path}: {core.label} ids can be given as {', '.join(core.lookup_keys)} or an Amass ID; got {key!r}"
                )
            if value is None or not str(value).strip():
                raise MonitorError(f"{path}: empty {key} in ids")
            entries.append((key, str(value).strip()))
        else:
            raise MonitorError(f"{path}: each id is an Amass ID or a single `idType: value` pair, got {item!r}")

    include = data.get("include") or []
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list):
        raise MonitorError(f"{path}: `include` must be a list")
    bad = [i for i in include if i not in core.include_allowed]
    if bad:
        raise MonitorError(
            f"{path}: include value(s) {', '.join(map(str, bad))} not supported for {core.label}; "
            f"allowed: {', '.join(core.include_allowed)}"
        )
    merged = list(core.include_default) + [i for i in include if i not in core.include_default]
    return {"name": name, "core": core, "entries": entries, "include": merged, "cadence": data.get("cadence")}


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _retry_after(headers: Any, attempt: int) -> float:
    value = headers.get("Retry-After") if headers else None
    if value:
        try:
            return max(1.0, float(value)) + 0.5
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(value)
                return max(1.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds()) + 0.5
            except (TypeError, ValueError):
                pass
    return min(60.0, 2.0**attempt)


class Client:
    """Thin REST client: auth, credit accounting, 429 backoff, transient retries."""

    def __init__(self, api_key: str, max_credits: float):
        self._api_key = api_key
        self.max_credits = max_credits
        self.credits = {"feed": 0.0, "get": 0.0, "lookup": 0.0}
        self.calls = {"feed": 0, "get": 0, "lookup": 0}
        self.rate_limit_retries = 0
        self.backoff_seconds = 0.0

    @property
    def spent(self) -> float:
        return sum(self.credits.values())

    def ensure_budget(self, needed: float, what: str) -> None:
        if self.spent + needed > self.max_credits:
            raise BudgetExceeded(
                f"{what} needs about {needed:g} more metered call(s); {self.spent:g} already made this run and "
                f"--max-credits is {self.max_credits:g}. Nothing more was fetched or stored. Re-run with "
                f"--max-credits {math.ceil(self.spent + needed)} or higher to allow it."
            )

    def _send(self, method: str, url: str, body: bytes | None):
        request = urllib.request.Request(url, data=body, method=method)
        request.add_header("Authorization", "Bearer " + self._api_key)
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", f"amass-watchlist-monitor/{VERSION}")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.headers, err.read()

    def request(self, method: str, path: str, kind: str, params: list | None = None, body: Any = None) -> Any:
        self.ensure_budget(1, f"a {kind} request")
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        payload = None if body is None else json.dumps(body).encode()
        transient = limited = 0
        while True:
            try:
                status, headers, raw = self._send(method, url, payload)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                transient += 1
                if transient > MAX_TRANSIENT_RETRIES:
                    reason = getattr(err, "reason", err)
                    raise MonitorError(f"network error on {path} after {MAX_TRANSIENT_RETRIES} retries: {reason}") from None
                time.sleep(2.0**transient)
                continue
            cost = headers.get("X-Amass-Credit-Cost") if headers else None
            if cost:
                try:
                    self.credits[kind] += float(cost)
                except ValueError:
                    pass
            if status == 429:
                limited += 1
                if limited > MAX_RATE_LIMIT_RETRIES:
                    raise ApiError(429, "TOO_MANY_REQUESTS", f"still rate limited after {limited - 1} retries", path)
                wait = _retry_after(headers, limited)
                self.rate_limit_retries += 1
                self.backoff_seconds += wait
                _log(f"  rate limited (429); waiting {wait:.0f}s as Retry-After asks")
                time.sleep(wait)
                continue
            if status >= 500 and transient < MAX_TRANSIENT_RETRIES:
                transient += 1
                time.sleep(2.0**transient)
                continue
            try:
                data = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                data = None
            if status >= 400:
                error = (data or {}).get("error") if isinstance(data, dict) else None
                error = error if isinstance(error, dict) else {}
                message = error.get("message") or raw[:200].decode("utf-8", "replace")
                raise ApiError(status, str(error.get("code") or "HTTP_ERROR"), message, path)
            self.calls[kind] += 1
            return data


# --------------------------------------------------------------------------
# State directory
# --------------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as err:
        raise MonitorError(f"state file {path} is corrupt ({err}); restore it or delete it to re-baseline") from None


class State:
    def __init__(self, directory: Path):
        self.dir = directory
        self.snapshots = directory / "snapshots"

    def read(self, name: str) -> Any:
        return _read_json(self.dir / name)

    def write(self, name: str, data: Any) -> None:
        _write_json(self.dir / name, data)

    def snapshot(self, amass_id: str) -> dict | None:
        return _read_json(self.snapshots / f"{amass_id}.json")

    def tombstone(self, amass_id: str) -> dict | None:
        return _read_json(self.snapshots / f"{amass_id}.deleted.json")

    def forget(self, amass_id: str) -> None:
        for name in (f"{amass_id}.json", f"{amass_id}.deleted.json"):
            try:
                (self.snapshots / name).unlink()
            except FileNotFoundError:
                pass

    def append_run(self, entry: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "runs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def digest_path(self, day: dt.date) -> Path:
        folder = self.dir / "digests"
        folder.mkdir(parents=True, exist_ok=True)
        path, n = folder / f"{day}.md", 1
        while path.exists():
            n += 1
            path = folder / f"{day}-{n}.md"
        return path


class Lock:
    """One run per watchlist state folder at a time."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> "Lock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_lock(self.path)
                if holder and _pid_alive(holder.get("pid")):
                    raise MonitorError(
                        f"another run (pid {holder.get('pid')}, started {holder.get('startedAt')}) holds {self.path}"
                    ) from None
                self.path.unlink(missing_ok=True)  # stale lock from a run that died
                continue
            with os.fdopen(fd, "w") as fh:
                json.dump({"pid": os.getpid(), "startedAt": _now_iso()}, fh)
            return self
        raise MonitorError(f"could not take the lock {self.path}")

    def __exit__(self, *exc: Any) -> None:
        self.path.unlink(missing_ok=True)


def _read_lock(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":  # on Windows, os.kill(pid, 0) would terminate the process instead of probing it
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: the process exists
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)




def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# Chunking and the feed
# --------------------------------------------------------------------------


def assign_chunks(previous: list[dict], desired: list[str]) -> tuple[list[dict], dict]:
    """Stable chunk assignment.

    First run: sort and slice by 100. Later runs keep every id in the chunk it
    already has; removed ids leave their chunk, added ids fill the first chunk
    with room (in sorted order), then open new chunks. So a watchlist edit
    changes only the chunks it touches.
    """
    want = set(desired)
    if not previous:
        chunks = [{"key": f"c{n}", "ids": desired[i : i + CHUNK_SIZE]} for n, i in enumerate(range(0, len(desired), CHUNK_SIZE))]
        return chunks, {"added": [], "removed": [], "touched": []}
    chunks, held, touched = [], set(), set()
    for chunk in previous:
        kept = [i for i in chunk["ids"] if i in want]
        held.update(chunk["ids"])
        if kept != chunk["ids"]:
            touched.add(chunk["key"])
        if kept:
            chunks.append({"key": chunk["key"], "ids": kept})
    added = sorted(want - held)
    removed = sorted(held - want)
    next_n = max(int(c["key"][1:]) for c in previous) + 1
    for amass_id in added:
        target = next((c for c in chunks if len(c["ids"]) < CHUNK_SIZE), None)
        if target is None:
            target = {"key": f"c{next_n}", "ids": []}
            next_n += 1
            chunks.append(target)
        target["ids"].append(amass_id)
        touched.add(target["key"])
    for chunk in chunks:
        chunk["ids"].sort()
    return chunks, {"added": added, "removed": removed, "touched": sorted(touched)}


def fingerprint(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]


@dataclasses.dataclass
class Event:
    """Everything the feed said about one record in this sweep, upserted."""

    amass_id: str
    state: str | None = None  # latest created / updated / deleted; None if only `unchanged`
    state_date: str | None = None
    create_date: str | None = None
    arrivals: int = 0
    effective: str = ""  # latest change date this sweep reported for the record
    sections: dict = dataclasses.field(default_factory=dict)
    kinds: set = dataclasses.field(default_factory=set)  # every updateType that arrived

    @property
    def kind(self) -> str:
        return self.state or "unchanged"

    def upsert(self, item: dict) -> None:
        self.arrivals += 1
        self.kinds.add(item.get("updateType"))
        if item.get("createDate"):
            self.create_date = item["createDate"]
        for section in item.get("documentSections") or []:
            sid = section.get("documentSectionId")
            if not sid:
                continue
            held = self.sections.get(sid)
            if held is None or _s(section.get("updateDate")) >= _s(held.get("updateDate")):
                self.sections[sid] = section
            self.effective = max(self.effective, _s(section.get("updateDate")))
        kind, date = item.get("updateType"), _s(item.get("updateDate"))
        if kind == "unchanged":
            # The record row did not move; its updateDate is the row's own last
            # write and says nothing about this delivery.
            return
        if self.state is None or date >= _s(self.state_date):
            self.state, self.state_date = kind, item.get("updateDate")
        self.effective = max(self.effective, date)


def sweep_chunk(client: Client, core: Core, ids: list[str], since: str) -> tuple[list[dict], int, bool]:
    """Page one chunk's scoped feed to nextCursor null. The id set is frozen."""
    frozen = tuple(ids)
    base = [("since", since), ("limit", PAGE_LIMIT)] + [("amassId", i) for i in frozen]
    arrivals: list[dict] = []
    cursor: str | None = None
    pages, restarted, seen = 0, False, set()
    while True:
        params = base + ([("cursor", cursor)] if cursor else [])
        try:
            page = client.request("GET", core.path("changes"), "feed", params)
        except ApiError as err:
            if err.status == 400 and cursor and not restarted:
                _log(
                    f"  the feed rejected the cursor ({err.message}). Restarting this chunk from since {since} "
                    "with no cursor; delivery is at-least-once, so nothing is lost."
                )
                arrivals, cursor, restarted, seen = [], None, True, set()
                continue
            raise
        pages += 1
        if not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise MonitorError(f"unexpected feed response on {core.path('changes')}")
        arrivals.extend(page["data"])
        cursor = page.get("nextCursor")
        if cursor is None:
            return arrivals, pages, restarted
        if cursor in seen:
            raise MonitorError(f"the feed returned the same cursor twice for chunk of {len(frozen)} ids; stopping")
        seen.add(cursor)


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _trunc(value: Any, n: int = 120) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


def fmt(value: Any) -> str:
    if value is None or value is MISSING:
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if value and len(value) <= 60 and "`" not in value and "\n" not in value:
            return f"`{value}`"
        return "“" + _trunc(value, 160) + "”"
    return "`" + _trunc(_canon(value), 120) + "`"


def get_path(record: dict, path: str) -> Any:
    head, *rest = path.split(".")
    if head not in record:
        return MISSING  # not requested when this record was fetched: not comparable
    value = record[head]
    for part in rest:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _listing(items: list[str], cap: int = 10) -> str:
    shown = ", ".join(items[:cap])
    return shown + (f" and {len(items) - cap} more" if len(items) > cap else "")


def _set_diff(old: Any, new: Any) -> tuple[list[str], list[str]]:
    o = {_canon(x) if not isinstance(x, str) else x for x in (old or [])}
    n = {_canon(x) if not isinstance(x, str) else x for x in (new or [])}
    return sorted(n - o), sorted(o - n)


def _keyed(items: Any, key: Callable) -> dict:
    out: dict = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        k = key(item)
        n = 0
        while (k, n) in out:
            n += 1  # duplicate keys keep their order of appearance
        out[(k, n)] = item
    return out


def _row_changes(name: str, old: list, new: list) -> str:
    """Same-length list of rows: name the rows that moved and how (first three)."""
    moved = []
    for n, (a, b) in enumerate(zip(old, new), 1):
        if _canon(a) == _canon(b):
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            where = a.get("group") or b.get("group") or f"row {n}"
            fields = [f"{k} {fmt(a.get(k))} → {fmt(b.get(k))}" for k in sorted(set(a) | set(b)) if _canon(a.get(k)) != _canon(b.get(k))]
            moved.append(f"{where}: {', '.join(fields)}")
        else:
            moved.append(f"row {n}: {fmt(a)} → {fmt(b)}")
    shown = "; ".join(moved[:3]) + (f"; and {len(moved) - 3} more row(s)" if len(moved) > 3 else "")
    return f"{name} ({shown})"


def _subfield_changes(old: dict, new: dict) -> list[str]:
    parts = []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name), new.get(name)
        if _canon(a) == _canon(b):
            continue
        if isinstance(a, list) or isinstance(b, list):
            a, b = a or [], b or []
            parts.append(f"{name} ({len(a)} → {len(b)} rows)" if len(a) != len(b) else _row_changes(name, a, b))
        else:
            parts.append(f"{name} {fmt(a)} → {fmt(b)}" if not isinstance(a or b, (dict, list)) else name)
    return parts


def _object_diff(old: Any, new: Any, prefix: str) -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        lines = []
        for name in sorted(set(old) | set(new)):
            lines += _object_diff(old.get(name), new.get(name), f"{prefix}.{name}")
        return lines
    if isinstance(old, list) or isinstance(new, list):
        added, removed = _set_diff(old, new)
        parts = ([f"added {_listing(added)}"] if added else []) + ([f"removed {_listing(removed)}"] if removed else [])
        return [f"{prefix}: {'; '.join(parts)}"] if parts else []
    if _canon(old) != _canon(new):
        return [f"{prefix}: {fmt(old)} → {fmt(new)}"]
    return []


def diff_rule(rule: Rule, old: Any, new: Any) -> list[tuple[str, str]]:
    """Returns (class, line) pairs; a line may carry indented sub-bullets."""
    name = rule.field
    if rule.kind == "scalar":
        return [] if _canon(old) == _canon(new) else [(rule.cls, f"{name}: {fmt(old)} → {fmt(new)}")]
    if rule.kind == "presence":
        was, now = bool(old), bool(new)
        return [] if was == now else [(rule.cls, f"{name}: {'present' if now else 'absent'} (was {'present' if was else 'absent'})")]
    if rule.kind == "text":
        if _canon(old) == _canon(new):
            return []
        return [(rule.cls, f"{name}: changed ({len(old or '')} → {len(new or '')} chars), now {fmt(new)}")]
    if rule.kind in ("set", "pubtypes"):
        added, removed = _set_diff(old, new)
        if not added and not removed:
            return []
        parts = ([f"added {_listing(added)}"] if added else []) + ([f"removed {_listing(removed)}"] if removed else [])
        cls = rule.cls
        if rule.kind == "pubtypes" and CORRECTION_TYPES & set(added + removed):
            cls = RETRACTIONS
        return [(cls, f"{name}: {'; '.join(parts)}")]
    if rule.kind == "keyed":
        before, after = _keyed(old, rule.key), _keyed(new, rule.key)
        added = [after[k] for k in after if k not in before]
        removed = [before[k] for k in before if k not in after]
        revised = [(before[k], after[k]) for k in after if k in before and _canon(before[k]) != _canon(after[k])]
        if not (added or removed or revised):
            return []
        counts = ", ".join(f"{n} {w}" for n, w in ((len(added), "added"), (len(revised), "revised"), (len(removed), "removed")) if n)
        subs = [f"added {rule.label(x)}" for x in added]
        subs += [f"revised {rule.label(b)}: {', '.join(_subfield_changes(a, b))}" for a, b in revised]
        subs += [f"removed {rule.label(x)}" for x in removed]
        shown = subs[:12] + ([f"… and {len(subs) - 12} more"] if len(subs) > 12 else [])
        return [(rule.cls, f"{name}: {counts}" + "".join(f"\n  - {s}" for s in shown))]
    if rule.kind == "object":
        lines = _object_diff(old, new, name)
        if not lines:
            return []
        shown = lines[:12] + ([f"… and {len(lines) - 12} more"] if len(lines) > 12 else [])
        return [(rule.cls, f"{name}: {len(lines)} change(s)" + "".join(f"\n  - {s}" for s in shown))]
    raise ValueError(rule.kind)


def link_rules(core: Core, include: list[str]) -> tuple[Rule, ...]:
    """Cross-link fields the caller asked for beyond the per-Core defaults."""
    known = {r.field for r in core.rules}
    return tuple(Rule(f, "set", LINKS) for f in include if re.fullmatch(r"references[A-Z]\w*Core", f) and f not in known)


def diff_records(core: Core, include: list[str], old: dict, new: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """Substantive changes, plus the names of unwatched fields that moved."""
    rules = core.rules + link_rules(core, include)
    changes: list[tuple[str, str]] = []
    for rule in rules:
        a, b = get_path(old, rule.field), get_path(new, rule.field)
        if a is MISSING or b is MISSING:
            continue
        changes += diff_rule(rule, a, b)
    watched = {r.field for r in rules} | set(core.metadata_fields) | {"documentSections"}
    moved = []
    for name in sorted(set(old) | set(new)):
        a, b = old.get(name, MISSING), new.get(name, MISSING)
        if a is MISSING or b is MISSING or _canon(a) == _canon(b):
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            moved += [
                f"{name}.{sub}"
                for sub in sorted(set(a) | set(b))
                if f"{name}.{sub}" not in watched and _canon(a.get(sub)) != _canon(b.get(sub))
            ]
        elif name not in watched:
            moved.append(name)
    return changes, moved


def _path_sort_key(path: Any) -> tuple:
    return tuple(int(p) if p.isdigit() else 10**6 for p in re.split(r"[^0-9]+", _s(path)) if p) or (10**6,)


DOC_ORDER = {"FDA_LABEL": 0, "EMA_SMPC": 1, "FDA_REVIEW": 2, "EMA_EPAR": 3}
SECTION_CAP = 12


def section_lines(old: dict | None, new: dict, feed_sections: dict) -> str | None:
    """RegulatoryCore: the sections the feed named, plus table-of-contents adds and drops.

    One summary line counted by document type, then the named sections, labels and
    SmPCs first. A feed section id the table of contents no longer lists (old or new)
    is counted, not listed: it has no name left to report.
    """
    toc_new = {s.get("documentSectionId"): s for s in new.get("documentSections") or [] if isinstance(s, dict)}
    toc_old = {s.get("documentSectionId"): s for s in (old or {}).get("documentSections") or [] if isinstance(s, dict)}
    counts: dict[tuple[str, str], int] = {}
    named: list[tuple[tuple, str]] = []
    unnamed = 0

    def note(sid: str, what: str) -> None:
        nonlocal unnamed
        s = toc_new.get(sid) or toc_old.get(sid)
        if not s:
            unnamed += 1
            return
        doc = _s(s.get("docType")) or "DOCUMENT"
        counts[(doc, what.split(" ")[0])] = counts.get((doc, what.split(" ")[0]), 0) + 1
        label = " ".join(x for x in (f"`{doc}`", s.get("path"), s.get("title")) if x)
        named.append(((DOC_ORDER.get(doc, 9), _path_sort_key(s.get("path")), _s(s.get("title"))), f"{label}: {what}"))

    for sid, event in feed_sections.items():
        note(sid, f"{event.get('updateType')} {event.get('updateDate') or ''}".rstrip())
    if old is not None:
        for sid in toc_new.keys() - toc_old.keys() - feed_sections.keys():
            note(sid, "added to the table of contents")
        for sid in toc_old.keys() - toc_new.keys() - feed_sections.keys():
            note(sid, "dropped from the table of contents")
        for sid in toc_new.keys() & toc_old.keys() - feed_sections.keys():
            moved = _subfield_changes(toc_old[sid], toc_new[sid])
            if moved:
                note(sid, "revised " + ", ".join(moved))
    if not named and not unnamed:
        return None
    summary = ", ".join(
        f"{doc} {n} {what}" for (doc, what), n in sorted(counts.items(), key=lambda kv: (DOC_ORDER.get(kv[0][0], 9), kv[0]))
    )
    if unnamed:
        summary += (", " if summary else "") + f"{unnamed} no longer in the table of contents"
    named.sort(key=lambda e: e[0])
    details = [line for _, line in named[:SECTION_CAP]]
    if len(named) > SECTION_CAP:
        details.append(f"… and {len(named) - SECTION_CAP} more named section(s)")
    return f"{len(named) + unnamed} document section(s): {summary}" + "".join(f"\n  - {d}" for d in details)


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------


@dataclasses.dataclass
class Report:
    amass_id: str
    title: str
    ident: str | None = None
    url: str | None = None
    note: str | None = None
    lines: dict = dataclasses.field(default_factory=dict)  # class -> [line]

    def add(self, cls: str, line: str | None = None) -> None:
        self.lines.setdefault(cls, [])
        if line:
            self.lines[cls].append(line)


def utc_today() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def parse_date(text: str, what: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise MonitorError(f"{what} must be a date as YYYY-MM-DD, got {text!r}") from None


class Run:
    def __init__(self, args: argparse.Namespace, watchlist: dict, state: State, client: Client):
        self.args = args
        self.wl = watchlist
        self.core: Core = watchlist["core"]
        self.state = state
        self.client = client
        self.today = utc_today()
        self.started = _now_iso()
        self.replay = parse_date(args.since, "--since") if args.since else None
        if self.replay and self.replay > self.today:
            raise MonitorError("--since cannot be in the future")
        self.notes: list[str] = []
        self.unresolved: list[str] = []
        self.errors: list[str] = []
        self.chunk_runs: list[dict] = []

    # -- 1. resolve --------------------------------------------------------
    def resolve(self, ids_state: dict) -> tuple[list[str], dict]:
        core = self.core
        direct, external = [], []
        bad = []
        for entry in self.wl["entries"]:
            if isinstance(entry, tuple):
                external.append(entry)
            elif core.valid_id(entry):
                direct.append(entry)
            else:
                other = next((label for prefix, label in PREFIX_TO_CORE.items() if entry.startswith(prefix)), None)
                bad.append(f"{entry!r} ({'belongs to ' + other if other and other != core.label else 'malformed'})")
        if bad:
            raise MonitorError(
                f"{len(bad)} id(s) in the watchlist are not valid {core.label} Amass IDs ({core.prefix}…): "
                + "; ".join(bad)
                + ". The feed rejects the whole request for one bad id, so the run stops here; nothing was sent."
            )
        cache: dict = ids_state.get("lookups") or {}
        keys = [f"{k}:{v}" for k, v in external]
        pending = [(k, v) for (k, v), ck in zip(external, keys) if ck not in cache]
        pending = list(dict.fromkeys(pending))
        if pending and self.args.dry_run:
            # A dry run resolves too, or it could not sweep these ids; it stores nothing,
            # so the first real run looks them up again.
            self.notes.append(
                f"Looked up {len(pending)} external id(s) for this dry run ({math.ceil(len(pending) / LOOKUP_BATCH)} "
                "request(s)); not stored, so the first real run looks them up again."
            )
        if pending:
            for start in range(0, len(pending), LOOKUP_BATCH):
                batch = pending[start : start + LOOKUP_BATCH]
                _log(f"looking up {len(batch)} external id(s)")
                data = self.client.request(
                    "POST", core.path("records", "lookup"), "lookup", body={"items": [{k: v} for k, v in batch]}
                )
                results = data.get("data") if isinstance(data, dict) else None
                if not isinstance(results, list) or len(results) != len(batch):
                    raise MonitorError("unexpected lookup response: item count does not match the request")
                for (k, v), item in zip(batch, results):
                    found = item.get("amassIds") or []
                    if item.get("error") or not found:
                        err = item.get("error") or {}
                        self.unresolved.append(f"{k} {v}: {err.get('code', 'NOT_FOUND')} {err.get('message', '')}".strip())
                        continue
                    wrong = [i for i in found if not core.valid_id(i)]
                    if wrong:
                        raise MonitorError(f"lookup of {k} {v} returned ids outside {core.label}: {wrong}")
                    cache[f"{k}:{v}"] = sorted(found)
                    if len(found) > 1:
                        self.notes.append(f"{k} {v} resolved to {len(found)} records; all {len(found)} are watched.")
        lookups = {ck: cache[ck] for ck in keys if ck in cache}
        resolved = {i for ids in lookups.values() for i in ids}
        desired = sorted(set(direct) | resolved)
        if not desired:
            raise MonitorError("nothing to watch: no id in the watchlist resolved to an Amass ID")
        return desired, lookups

    # -- whole run -----------------------------------------------------------
    def execute(self) -> int:
        core, state = self.core, self.state
        ids_state = state.read("ids.json") or {}
        if ids_state.get("core") not in (None, core.name):
            raise MonitorError(f"{state.dir} holds a {ids_state['core']} watchlist, not {core.name}; use another name")
        since_state = (state.read("since.json") or {}).get("chunks") or {}

        desired, lookups = self.resolve(ids_state)
        chunks, edits = assign_chunks(ids_state.get("chunks") or [], desired)
        if edits["added"] or edits["removed"]:
            self.notes.append(
                f"Watchlist edited since the last run: {len(edits['added'])} added, {len(edits['removed'])} removed; "
                f"chunk(s) changed: {', '.join(edits['touched']) or 'none'}."
            )

        # -- 3. sweep
        self.client.ensure_budget(len(chunks), "sweeping the feed (one page per chunk at least)")
        events: dict[str, Event] = {}
        chunk_runs = self.chunk_runs
        for chunk in chunks:
            stored = (since_state.get(chunk["key"]) or {}).get("since")
            if self.replay:
                since = self.replay
            elif stored:
                since = parse_date(stored, "stored since") - dt.timedelta(days=RESUME_OVERLAP_DAYS)
            else:
                since = self.today - dt.timedelta(days=RESUME_OVERLAP_DAYS)
            _log(f"sweeping chunk {chunk['key']} ({len(chunk['ids'])} ids) from {since}")
            arrivals, pages, restarted = sweep_chunk(self.client, core, chunk["ids"], since.isoformat())
            members = set(chunk["ids"])
            for item in arrivals:
                amass_id = item.get("amassId")
                if amass_id not in members:
                    self.notes.append(f"The feed returned {amass_id}, which is not in chunk {chunk['key']}; ignored.")
                    continue
                events.setdefault(amass_id, Event(amass_id)).upsert(item)
            if restarted:
                self.notes.append(f"Chunk {chunk['key']}: the feed rejected a cursor mid-sweep; the chunk was re-swept from {since}.")
            chunk_runs.append(
                {"key": chunk["key"], "ids": len(chunk["ids"]), "sweptFrom": since.isoformat(), "storedSince": stored,
                 "pages": pages, "arrivals": len(arrivals), "restarted": restarted}
            )
            _log(f"  {pages} page(s), {len(arrivals)} event(s)")

        # -- 4. classify
        window = {c["key"]: c["sweptFrom"] for c in chunk_runs}
        chunk_of = {i: c["key"] for c in chunks for i in c["ids"]}
        snaps = {i: state.snapshot(i) for i in desired}
        tombs = {i: state.tombstone(i) for i in desired}
        baseline, changed, removed, applied, skipped_removed = [], [], [], [], []
        for amass_id in desired:
            event, snap, tomb = events.get(amass_id), snaps[amass_id], tombs[amass_id]
            if event and event.kind == "deleted":
                if tomb and tomb.get("removedOn") == event.state_date and not self.replay:
                    applied.append(amass_id)
                else:
                    removed.append(amass_id)
            elif snap is None:
                if tomb and not event:
                    skipped_removed.append(amass_id)
                else:
                    baseline.append(amass_id)
            elif event is None:
                continue
            elif not self.replay and not tomb and snap.get("appliedThrough") and event.effective <= snap["appliedThrough"]:
                applied.append(amass_id)
            else:
                changed.append(amass_id)
        to_fetch = sorted(baseline + changed)

        if self.args.dry_run:
            return self.finish_dry_run(chunk_runs, events, baseline, changed, removed, applied)

        # -- 5. hydrate
        self.client.ensure_budget(len(to_fetch), f"fetching {len(to_fetch)} record(s) ({len(baseline)} baseline, {len(changed)} changed)")
        include = self.wl["include"]
        params = [("include", i) for i in include]
        fresh: dict[str, dict] = {}
        failed_chunks: set[str] = set()
        for n, amass_id in enumerate(to_fetch, 1):
            if n == 1 or n % 25 == 0 or n == len(to_fetch):
                _log(f"fetching record {n}/{len(to_fetch)}")
            try:
                body = self.client.request("GET", core.path("records", amass_id), "get", params)
            except BudgetExceeded:
                raise
            except MonitorError as err:
                hint = " (it may have been removed; the deletion is not on the feed yet)" if isinstance(err, ApiError) and err.status == 404 else ""
                self.errors.append(f"`{amass_id}`: {err}{hint}")
                failed_chunks.add(chunk_of[amass_id])
                continue
            record = body.get("data") if isinstance(body, dict) else None
            if not isinstance(record, dict) or record.get("amassId") != amass_id:
                self.errors.append(f"`{amass_id}`: unexpected record response")
                failed_chunks.add(chunk_of[amass_id])
                continue
            fresh[amass_id] = record

        # -- 5b. diff and classify
        reports: dict[str, Report] = {}

        def report_for(amass_id: str, record: dict | None) -> Report:
            if amass_id not in reports:
                record = record or {}
                reports[amass_id] = Report(
                    amass_id,
                    title=_trunc(core.title(record) or amass_id, 140),
                    ident=core.ident(record),
                    url=record.get("sourceUrl") or record.get("url"),
                )
            return reports[amass_id]

        for amass_id in removed:
            event = events[amass_id]
            last = (snaps[amass_id] or {}).get("record")
            r = report_for(amass_id, last)
            r.add(REMOVED, f"feed `deleted`, removal observed {event.state_date}" + ("" if last else "; never captured"))

        for amass_id in to_fetch:
            record = fresh.get(amass_id)
            if record is None:
                continue
            event, snap = events.get(amass_id), snaps[amass_id]
            window_start = window[chunk_of[amass_id]]
            create = record.get("createDate") or (event.create_date if event else None)
            r = report_for(amass_id, record)
            if snap is None:
                r.add(BASELINE, core.state(record) or None)
                if create and create >= window_start:
                    r.add(NEW, f"entered Amass {create} (window from {window_start})")
                continue
            old = snap.get("record") or {}
            fetched_on = _s(snap.get("fetchedAt"))[:10]
            if tombs[amass_id]:
                r.add(NEW if create and create >= window_start else OTHER, f"back on the source after a removal on {tombs[amass_id].get('removedOn')}")
            elif create and create >= window_start and create > fetched_on:
                r.add(NEW, f"entered Amass {create} (window from {window_start})")
            if event:
                r.note = "feed " + ", ".join(f"`{k}`" for k in sorted(event.kinds)) + f" {event.state_date or event.effective}"
            changes, moved = diff_records(core, include, old, record)
            for cls, line in changes:
                r.add(cls, line)
            if core is REGULATORYCORE:
                block = section_lines(old, record, event.sections if event else {})
                if block:
                    if event and event.kind == "unchanged":
                        block = "record row unchanged (feed `unchanged`); " + block
                    r.add(LABEL, block)
            if core is TRIALCORE and any(cls == RESULTS for cls, _ in changes) and event:
                if _s(event.effective) > _s(record.get("lastUpdateDate")):
                    r.add(RESULTS, f"results-only revision: feed event {event.effective}, record lastUpdateDate still {record.get('lastUpdateDate')}")
            for name in core.metadata_fields:
                a, b = old.get(name, MISSING), record.get(name, MISSING)
                if a is not MISSING and b is not MISSING and _canon(a) != _canon(b):
                    r.add(METADATA, f"{name}: {fmt(a)} → {fmt(b)}")
            if not (SUBSTANTIVE | {NEW}) & set(r.lines):
                if moved:
                    shown = [f"{m} {fmt(get_path(old, m))} → {fmt(get_path(record, m))}" if m == "lastUpdateDate" else m for m in moved]
                    r.add(METADATA, "unwatched fields moved: " + _listing(shown, 8))
                elif not r.lines.get(METADATA):
                    r.add(METADATA, "no field in the record changed")
        for amass_id in skipped_removed:
            self.notes.append(f"`{amass_id}` was removed from the source earlier and has no snapshot; not fetched.")

        # -- 6. digest
        digest_path = state.digest_path(self.today)
        digest = self.render_digest(chunk_runs, reports, events, fresh, edits, applied, baseline, changed, removed)
        digest_path.write_text(digest, encoding="utf-8")

        # -- 7. persist
        now = _now_iso()
        for amass_id, record in fresh.items():
            event, snap = events.get(amass_id), snaps[amass_id]
            through = max(_s((snap or {}).get("appliedThrough")), event.effective if event else "") or None
            _write_json(
                state.snapshots / f"{amass_id}.json",
                {"amassId": amass_id, "fetchedAt": now, "include": self.wl["include"], "appliedThrough": through, "record": record},
            )
            if tombs[amass_id]:
                (state.snapshots / f"{amass_id}.deleted.json").unlink(missing_ok=True)
        for amass_id in removed:
            _write_json(
                state.snapshots / f"{amass_id}.deleted.json",
                {"amassId": amass_id, "removedOn": events[amass_id].state_date, "observedAt": now},
            )
        for amass_id in edits["removed"]:
            state.forget(amass_id)
        new_since = {}
        for chunk, info in zip(chunks, chunk_runs):
            if chunk["key"] in failed_chunks:
                if chunk["key"] in since_state:
                    new_since[chunk["key"]] = since_state[chunk["key"]]
                continue
            new_since[chunk["key"]] = {
                "since": self.today.isoformat(),
                "sweptFrom": info["sweptFrom"],
                "completedAt": now,
                "size": len(chunk["ids"]),
                "fingerprint": fingerprint(chunk["ids"]),
            }
            info["completed"] = True
        state.write("since.json", {"chunks": new_since})
        state.write("ids.json", {"core": core.name, "lookups": lookups, "chunks": chunks, "updatedAt": now})
        self.log_run("ok" if not self.errors else "partial", chunk_runs, events, baseline, changed, removed, applied, digest_path)
        self.print_summary(reports, chunk_runs, events, baseline, changed, removed, applied, digest_path)
        return 0 if not self.errors else 1

    # -- output --------------------------------------------------------------
    def counts(self, reports: dict[str, Report]) -> dict[str, int]:
        return {cls: sum(1 for r in reports.values() if cls in r.lines) for cls in CLASS_ORDER}

    def render_digest(self, chunk_runs, reports, events, fresh, edits, applied, baseline, changed, removed) -> str:
        core = self.core
        sweeps = sorted({c["sweptFrom"] for c in chunk_runs})
        if self.replay:
            window = f"from {self.replay} (replay requested with --since)"
        elif len(sweeps) == 1:
            window = f"from {sweeps[0]} (two days before each chunk's stored since, or before today for new chunks)"
        else:
            window = "per chunk: " + ", ".join(f"{c['key']} from {c['sweptFrom']}" for c in chunk_runs)
        n_ids = sum(c["ids"] for c in chunk_runs)
        out = [
            f"# Watchlist digest: {self.wl['name']}",
            "",
            f"**Core:** {core.label} · **Run:** {self.started.replace('T', ' ').replace('Z', ' UTC')} · "
            f"**Records watched:** {n_ids} in {len(chunk_runs)} chunk(s)",
            "",
            f"**Feed window:** {window}",
            "",
        ]
        counts = self.counts(reports)
        substantive = sum(1 for r in reports.values() if (SUBSTANTIVE | {REMOVED}) & set(r.lines))
        if not any(counts[c] for c in CLASS_ORDER if c != BASELINE):
            if counts[BASELINE]:
                out += [f"**First look:** {counts[BASELINE]} baseline snapshot(s) captured, nothing to compare yet. "
                        "Changes are reported from the next run.", ""]
            else:
                out += [f"**No changes** on the watched records since {sweeps[0] if len(sweeps) == 1 else 'the stored since'}.", ""]
        if self.unresolved:
            out += ["## Unresolved identifiers", ""] + [f"- {u}" for u in self.unresolved] + [""]
        if self.errors:
            out += ["## Errors", ""] + [f"- {e}" for e in self.errors] + [""]
        if self.notes:
            out += ["## Notes", ""] + [f"- {n}" for n in self.notes] + [""]
        if any(counts.values()):
            out += [f"## {core.label}", ""]
        for cls in CLASS_ORDER:
            rows = sorted((r for r in reports.values() if cls in r.lines), key=lambda r: (r.title.lower(), r.amass_id))
            if not rows:
                continue
            out += [f"### {cls} ({len(rows)})", ""]
            cap = DIGEST_CAPS.get(cls)
            for r in rows[:cap] if cap else rows:
                head = f"- **{r.title}** · `{r.amass_id}`"
                if r.ident:
                    head += f" · {r.ident}"
                if r.url:
                    head += f" · {r.url}"
                if r.note and cls not in (BASELINE, NEW):
                    head += f" · {r.note}"
                lines = r.lines[cls]
                if cls == BASELINE and lines:
                    out.append(head + f" · {lines[0]}")
                    continue
                out.append(head)
                for line in lines:
                    first, *rest = line.split("\n")
                    out.append(f"  - {first}")
                    out += [f"  {x}" for x in rest]
            if cap and len(rows) > cap:
                out.append(f"- … and {len(rows) - cap} more")
            out.append("")
        arrivals = sum(c["arrivals"] for c in chunk_runs)
        pages = sum(c["pages"] for c in chunk_runs)
        out += ["## Summary", ""]
        if any(counts.values()):
            out += ["| Class | Records |", "| --- | ---: |"] + [f"| {cls} | {counts[cls]} |" for cls in CLASS_ORDER if counts[cls]] + [""]
        out += [
            f"- Records with substantive changes or removals: {substantive}",
            f"- Feed: {pages} page(s), {arrivals} event(s) on {len(events)} record(s); "
            f"{len(applied)} already reflected in the stored snapshots",
            f"- Fetched: {len(fresh)} record(s) ({len(baseline)} baseline, {len(changed)} with feed events); "
            f"{len(removed)} removal(s) need no fetch",
        ]
        if self.client.rate_limit_retries:
            out.append(
                f"- Rate limiting: {self.client.rate_limit_retries} retry(ies) after HTTP 429, "
                f"{self.client.backoff_seconds:.0f}s waited"
            )
        out += [f"- Next expected refresh: {next_refresh(core, self.today)}", ""]
        return "\n".join(out)

    def log_run(self, status, chunk_runs, events, baseline, changed, removed, applied, digest_path, error=None) -> None:
        entry = {
            "runAt": self.started,
            "finishedAt": _now_iso(),
            "watchlist": self.wl["name"],
            "core": self.core.name,
            "mode": "dry-run" if self.args.dry_run else "run",
            "status": status,
            "replaySince": self.replay.isoformat() if self.replay else None,
            "chunks": chunk_runs,
            "events": sum(c.get("arrivals", 0) for c in chunk_runs),
            "eventRecords": len(events),
            "alreadyApplied": len(applied),
            "baseline": len(baseline),
            "changed": len(changed),
            "removed": len(removed),
            "fetched": self.client.calls["get"],
            "lookups": self.client.calls["lookup"],
            "credits": {"total": self.client.spent, **self.client.credits},
            "rateLimitRetries": self.client.rate_limit_retries,
            "backoffSeconds": round(self.client.backoff_seconds, 1),
            "digest": str(digest_path.relative_to(self.state.dir)) if digest_path else None,
            "errors": self.errors + ([error] if error else []),
        }
        self.state.append_run(entry)

    def print_summary(self, reports, chunk_runs, events, baseline, changed, removed, applied, digest_path) -> None:
        counts = self.counts(reports)
        moved = [f"{counts[c]} {c.lower()}" for c in CLASS_ORDER if counts[c] and c != BASELINE]
        headline = ", ".join(moved) if moved else "no changes"
        if counts[BASELINE]:
            headline += f"; {counts[BASELINE]} baseline captured"
        print(
            f"amass-watchlist-monitor: {self.wl['name']} ({self.core.label}): {headline}. "
            f"Digest: {digest_path}"
        )
        print(f"  feed: {sum(c['pages'] for c in chunk_runs)} page(s), {sum(c['arrivals'] for c in chunk_runs)} event(s) "
              f"on {len(events)} record(s), {len(applied)} already applied")
        print(f"  fetched: {self.client.calls['get']} ({len(baseline)} baseline, {len(changed)} changed); removed: {len(removed)}")
        if self.client.rate_limit_retries:
            print(f"  rate limited: {self.client.rate_limit_retries} retry(ies), {self.client.backoff_seconds:.0f}s waited")
        for u in self.unresolved:
            print(f"  unresolved (not watched): {u}")
        for e in self.errors:
            print(f"  error: {e}")

    def finish_dry_run(self, chunk_runs, events, baseline, changed, removed, applied) -> int:
        """Report feed activity and what a real run would fetch, without fetching anything.

        On a new watchlist this is the preview: which records changed in the window
        and when. It cannot say what changed; there is no stored copy to compare yet.
        """
        n_ids = sum(c["ids"] for c in chunk_runs)
        starts = sorted({c["sweptFrom"] for c in chunk_runs})
        looked_up = " (external ids looked up, see note)" if self.client.calls["lookup"] else ""
        print(f"DRY RUN: {self.wl['name']} ({self.core.label}), {n_ids} records. Feed swept{looked_up}; no record fetched, nothing stored.")
        for c in chunk_runs:
            print(f"  chunk {c['key']} ({c['ids']} ids) from {c['sweptFrom']}: {c['pages']} page(s), {c['arrivals']} event(s)")
        for note in self.notes:
            print(f"  note: {note}")
        for u in self.unresolved:
            print(f"  unresolved (would not be watched): {u}")

        since = starts[0] if len(starts) == 1 else "each chunk's start"
        print(f"Feed activity since {since}: {len(events)} of {n_ids} record(s) changed (latest change per record).")
        status = {i: "no stored copy yet, first run captures it" for i in baseline}
        status |= {i: "a real run fetches and compares it" for i in changed}
        status |= {i: "removed from the source" for i in removed}
        status |= {i: "already in the stored copy" for i in applied}
        window_start = self.replay or (dt.date.fromisoformat(starts[0]) if len(starts) == 1 else None)
        active = sorted(events, key=lambda i: (events[i].state_date or events[i].effective or "", i), reverse=True)
        for amass_id in active[:40]:
            e = events[amass_id]
            what = ", ".join(sorted(e.kinds))
            sections = f", {len(e.sections)} document section(s)" if e.sections else ""
            new = ", new to Amass" if window_start and _s(e.create_date) >= window_start.isoformat() else ""
            print(f"  {e.state_date or e.effective}  {amass_id}  {what}{sections}{new}  ({status.get(amass_id, 'not fetched')})")
        if len(active) > 40:
            print(f"  … and {len(active) - 40} more")
        print(f"No feed activity: {n_ids - len(events)} record(s).")
        by_day: dict[str, int] = {}
        for e in events.values():
            day = e.state_date or e.effective
            by_day[day] = by_day.get(day, 0) + 1
        for day, n in sorted(by_day.items()):
            if n >= 3 and n >= 0.3 * len(events):
                print(f"  note: {n} records share {day}. Many records moving on one day is usually an Amass-wide refresh "
                      "(reprocessing, citation counts) rather than news about each record; a real run separates substantive "
                      "changes from Metadata-only ones.")

        fetch = len(baseline) + len(changed)
        lookups = self.client.calls["lookup"]
        next_run = fetch + len(chunk_runs) + lookups
        print(f"Next real run: fetches {fetch} record(s) ({len(baseline)} baseline, {len(changed)} changed), "
              f"about {next_run} call(s) with the feed pages" + (" and the lookup." if lookups else "."))
        if baseline:
            print("  A record with no stored copy is captured as a baseline: that run reports no change for it; "
                  "changes show from the following run.")
        days = (self.today - window_start).days if window_start else 0
        if days >= 7:
            every = 1 if self.core.name in ("trialcore", "biomedcore") else 7
            pages = max(len(chunk_runs), sum(c["pages"] for c in chunk_runs) * every / days)
            records = len(events) * every / days
            print(
                f"Ongoing, at the recommended {'daily' if every == 1 else 'weekly'} cadence and the activity seen here: "
                f"about {pages:.2g} feed page(s) and {records:.2g} changed record(s) a run. A floor: the feed shows each "
                "record's latest change only."
            )
        self.log_run("dry-run", chunk_runs, events, baseline, changed, removed, applied, None)
        return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monitor.py", description="Report what changed on a watchlist of Amass records.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="sweep the feed, fetch what moved, write the digest")
    run.add_argument("watchlist", help="watchlist file (.yaml or .json)")
    run.add_argument("--dry-run", action="store_true", help="sweep the feed and list what would be fetched; spends feed pages only")
    run.add_argument("--since", metavar="YYYY-MM-DD", help="replay the feed from this date instead of the stored since")
    run.add_argument("--state-dir", metavar="PATH", help="state root (default: .amass-monitor next to the watchlist)")
    run.add_argument("--max-credits", type=float, default=DEFAULT_MAX_CREDITS, metavar="N",
                     help=f"safety limit: stop before the run's metered calls exceed N (default {DEFAULT_MAX_CREDITS})")
    args = parser.parse_args(argv)

    try:
        watchlist_path = Path(args.watchlist)
        watchlist = load_watchlist(watchlist_path)
        api_key = os.environ.get("AMASS_API_KEY", "").strip()
        if not api_key:
            raise MonitorError("AMASS_API_KEY is not set. Export your Amass API key (platform.amass.tech/api-keys) and run again.")
        root = Path(args.state_dir) if args.state_dir else watchlist_path.resolve().parent / ".amass-monitor"
        state = State(root / watchlist["name"])
        client = Client(api_key, args.max_credits)
        runner = Run(args, watchlist, state, client)
        with Lock(state.dir / ".lock"):
            try:
                return runner.execute()
            except BudgetExceeded as err:
                print(f"STOPPED by the run's safety limit (--max-credits): {err}")
                runner.log_run("stopped-budget", runner.chunk_runs, {}, [], [], [], [], None, error=str(err))
                return 2
            except MonitorError as err:
                runner.log_run("error", runner.chunk_runs, {}, [], [], [], [], None, error=str(err))
                raise
    except MonitorError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
