"""Input and output guardrails.

Three tiers, cheapest first: deterministic input checks before any LLM call, a
grounding gate inside the graph, and output checks against the evidence that was
actually retrieved.

The design principle throughout is that **confidence and grounding are computed from
evidence, not self-reported**. Models are poorly calibrated about their own certainty;
the retrieved rows know exactly how complete they are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final

from app.agent.schemas import AnalystDraft, Confidence

# --------------------------------------------------------------------------
# Tier 1 — input
# --------------------------------------------------------------------------

MIN_QUERY_LENGTH: Final[int] = 3
MAX_QUERY_LENGTH: Final[int] = 2_000

INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\s+(all\s+)?(previous|prior|above|earlier)\b",
        r"\bdisregard\s+(all\s+)?(previous|prior|above|your)\b",
        (
            r"\b(reveal|show|print|repeat|output)\s+(me\s+)?(your\s+)?"
            r"(system\s+prompt|instructions|initial\s+prompt|rules)\b"
        ),
        r"\byou\s+are\s+now\b",
        r"\bact\s+as\s+(if\s+you\s+are\s+)?a\b",
        r"\bforget\s+(your|all|everything)\b",
        r"^\s*(system|assistant|developer)\s*:",
        r"<\s*/?\s*(system|im_start|im_end)\s*>",
    )
)

# Personalised-advice seeking. Not refused — the brief expects the analysis — but the
# not-advice caveat is forced onto the response.
ADVICE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bshould\s+i\s+(buy|sell|invest|put|hold)\b",
        r"\bhow\s+much\s+should\s+i\b",
        r"\bmy\s+(portfolio|savings|retirement|401k|pension)\b",
        r"\bis\s+(this|it)\s+a\s+good\s+stock\s+for\s+me\b",
        r"\bwhat\s+should\s+i\s+do\s+with\s+my\b",
    )
)

FINANCIAL_INTENT: Final[tuple[str, ...]] = (
    "company", "companies", "sector", "stock", "share", "invest", "buy", "sell",
    "margin", "revenue", "growth", "valuation", "multiple", "earnings", "profit",
    "debt", "leverage", "cash", "dividend", "yield", "beta", "risk", "holding",
    "portfolio", "acquire", "buyout", "target", "headcount", "hiring", "employee",
    "market", "cap", "ebitda", "data", "which", "compare", "exit", "deal",
)

PII_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("phone", re.compile(r"\b(?:\+?\d{1,3}[\s-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
)

REFUSAL_INJECTION: Final[str] = (
    "I can only answer questions about the companies and sectors in this dataset, "
    "using the data it holds. Ask me about the financials, margins, valuation or "
    "headcount of the companies in the selected sector and I'll work from the "
    "database."
)

REFUSAL_OFF_TOPIC: Final[str] = (
    "That's outside what I cover. I'm a financial analyst working from a database of "
    "public-company financials across technology, retail, manufacturing and logistics. "
    "Ask me about those companies — their margins, growth, valuation, leverage or "
    "headcount — and I'll answer from the data."
)

NOT_ADVICE_CAVEAT: Final[str] = (
    "This is analysis of the dataset, not personalised investment advice."
)


@dataclass(slots=True)
class InputGuardResult:
    """Outcome of the pre-LLM checks."""

    allowed: bool
    flags: list[str] = field(default_factory=list)
    refusal: str | None = None
    sanitised_query: str = ""
    requires_advice_caveat: bool = False


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Replace PII with typed placeholders, returning the kinds found.

    Applied before anything is logged or sent to a tracing backend. The raw query
    still reaches the LLM — the user asked their question and it must be answered —
    but it must not be persisted anywhere in raw form.
    """
    found: list[str] = []
    redacted = text
    for kind, pattern in PII_PATTERNS:
        if pattern.search(redacted):
            found.append(kind)
            redacted = pattern.sub(f"[{kind} redacted]", redacted)
    return redacted, found


def check_input(query: str) -> InputGuardResult:
    """Deterministic checks that run before any token is spent."""
    flags: list[str] = []
    stripped = query.strip()

    if len(stripped) < MIN_QUERY_LENGTH:
        return InputGuardResult(
            allowed=False,
            flags=["too_short"],
            refusal="Please ask a fuller question — that was too short to interpret.",
            sanitised_query=stripped,
        )
    if len(stripped) > MAX_QUERY_LENGTH:
        return InputGuardResult(
            allowed=False,
            flags=["too_long"],
            refusal=f"That question is too long. Please keep it under "
            f"{MAX_QUERY_LENGTH} characters.",
            sanitised_query=stripped[:MAX_QUERY_LENGTH],
        )

    sanitised, pii_kinds = redact_pii(stripped)
    if pii_kinds:
        flags.extend(f"pii_{kind}" for kind in pii_kinds)

    if any(pattern.search(stripped) for pattern in INJECTION_PATTERNS):
        return InputGuardResult(
            allowed=False,
            flags=[*flags, "injection"],
            refusal=REFUSAL_INJECTION,
            sanitised_query=sanitised,
        )

    lowered = stripped.lower()
    has_financial_intent = any(term in lowered for term in FINANCIAL_INTENT)
    # A bare ticker or company name is a legitimate question even with no other
    # financial vocabulary, so an upper-case token rescues it from the off-topic path.
    looks_like_entity = bool(re.search(r"\b[A-Z]{2,5}\b", stripped))
    if not has_financial_intent and not looks_like_entity:
        return InputGuardResult(
            allowed=False,
            flags=[*flags, "off_topic"],
            refusal=REFUSAL_OFF_TOPIC,
            sanitised_query=sanitised,
        )

    seeks_advice = any(pattern.search(stripped) for pattern in ADVICE_PATTERNS)
    if seeks_advice:
        flags.append("advice_seeking")

    return InputGuardResult(
        allowed=True,
        flags=flags,
        sanitised_query=sanitised,
        requires_advice_caveat=seeks_advice,
    )


# --------------------------------------------------------------------------
# Tier 3 — output, checked against retrieved evidence
# --------------------------------------------------------------------------

STALENESS_DAYS: Final[int] = 90

# Numbers that are never financial claims and must not be treated as fabricated.
_YEAR = re.compile(r"^(19|20)\d{2}$")
# A minus sign only counts as a sign when it is not acting as a range separator:
# "15-16x" is a range, not the number -16. Requires a non-digit before the sign.
_NUMBER = re.compile(r"(?<![\d.])-?\d[\d,]*\.?\d*")

# Ordinary finance vocabulary that a naive ticker regex would flag as an invented
# company. Checked before claiming fabrication.
NON_TICKER_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "EBITDA", "EV", "PE", "P", "E", "ROE", "ROI", "FCF", "USD", "TTM", "YOY",
        "CAGR", "LBO", "IPO", "M", "A", "R", "D", "CEO", "CFO", "SEC", "GAAP",
        "US", "UK", "EU", "I", "II", "III", "IV", "Q", "H", "FY", "NULL", "N",
        "AI", "IT", "SG", "COGS", "CAPEX", "OPEX", "DE", "NA", "TBD",
    }
)


@dataclass(slots=True)
class OutputGuardResult:
    """Outcome of the post-generation checks."""

    confidence: Confidence
    confidence_reason: str
    caveats: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    unverified_tickers: list[str] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)


def _evidence_tickers(evidence: list[dict[str, Any]]) -> set[str]:
    return {
        str(row["ticker"]).upper()
        for row in evidence
        if isinstance(row, dict) and row.get("ticker")
    }


def _evidence_numbers(evidence: list[dict[str, Any]]) -> list[float]:
    numbers: list[float] = []
    for row in evidence:
        if not isinstance(row, dict):
            continue
        for value in row.values():
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                numbers.append(float(value))
    return numbers


def _derived_values(evidence: list[dict[str, Any]]) -> list[float]:
    """Quantities an answer can legitimately compute from a single company's row.

    Differences between two fields of the same company — "the 28-point gap between
    gross and operating margin" — are ordinary analyst arithmetic, and the prompt
    explicitly asks for computed figures to be labelled as such. Without this they
    read as fabrications, which puts a spurious caveat on a correct answer and drops
    its confidence.

    Restricted to within-row pairs on purpose: differences across unrelated companies
    are not a quantity anyone reports, and admitting them would weaken the check
    towards accepting almost any number.
    """
    derived: list[float] = []
    for row in evidence:
        if not isinstance(row, dict):
            continue
        numbers = [
            float(v)
            for v in row.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None
        ]
        for i, left in enumerate(numbers):
            for right in numbers[i + 1 :]:
                derived.append(abs(left - right))
    return derived


def _matches_evidence(candidate: float, evidence_numbers: list[float]) -> bool:
    """Whether a stated figure traces to a retrieved value.

    Deliberately generous about *scale*. The answer legitimately restates a fraction
    as a percentage (0.662 -> 66.2), scales to billions (3_710_000_000 -> 3.71) and
    rounds. A strict equality check against raw values would flag almost every correct
    answer, which would make the caveat list noise and train the reader to ignore it.
    """
    if candidate == 0:
        return True
    for value in evidence_numbers:
        if value == 0:
            continue
        for scale in (1.0, 0.01, 100.0, 1e-3, 1e-6, 1e-9, 1e3, 1e6, 1e9):
            scaled = value * scale
            if scaled == 0:
                continue
            if abs(candidate - scaled) <= abs(scaled) * 0.02:
                return True
    return False


def _matches_derived(candidate: float, derived_numbers: list[float]) -> bool:
    """Whether a figure is a difference the answer computed from one company's row.

    Much stricter than ``_matches_evidence``: only the identity and percentage-point
    scales, and a 0.5% tolerance. Derived values are numerous — every pair of fields
    in every row — so granting them the full scale ladder and a loose tolerance makes
    the guard toothless. Measured: with the wide ladder, a genuine fabrication of
    "$87.4 billion" was silently accepted because one derived difference happened to
    land within 2% of it once multiplied by 100.
    """
    if candidate == 0:
        return True
    for value in derived_numbers:
        if value == 0:
            continue
        for scale in (1.0, 100.0):
            scaled = value * scale
            if scaled and abs(candidate - scaled) <= abs(scaled) * 0.005:
                return True
    return False


# Text that contains digits but never contains a *financial claim*: URLs, ISO dates,
# and hyphenated identifiers such as an SEC accession number. Grounding answers cite
# all three by design ("as of 2026-01-25, accession 0001045810-26-000021,
# https://sec.gov/..."), and scanning them for figures yields fragments like "-26"
# and "0000104169" that match nothing and read as fabrication.
_IDENTIFIER_NOISE = re.compile(
    r"https?://\S+"              # URLs
    r"|\b\d{4}-\d{2}-\d{2}\b"    # ISO dates
    r"|\b[\d-]{8,}\b"            # accession numbers, long digit strings
)


def strip_identifier_noise(text: str) -> str:
    """Blank out spans that carry digits but never carry a claim."""
    return _IDENTIFIER_NOISE.sub(" ", text)


def find_unverified_numbers(answer: str, evidence: list[dict[str, Any]]) -> list[str]:
    """Figures in the answer that do not trace to any retrieved value.

    Years, small counts and ordinals are excluded — "three companies", "2026" and
    "the first" are not financial claims, and flagging them produced a caveat on
    every answer during development.
    """
    evidence_numbers = _evidence_numbers(evidence)
    if not evidence_numbers:
        return []
    derived_numbers = _derived_values(evidence)

    unverified: list[str] = []
    for raw in _NUMBER.findall(strip_identifier_noise(answer)):
        cleaned = raw.replace(",", "").rstrip(".")
        if not cleaned or cleaned in {"-", "."}:
            continue
        if _YEAR.match(cleaned):
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        # Small integers are counts and ordinals, not financial magnitudes.
        if abs(value) < 10 and value == int(value):
            continue
        if not _matches_evidence(value, evidence_numbers) and not _matches_derived(
            value, derived_numbers
        ):
            unverified.append(raw)
    return unverified


def find_unverified_tickers(
    companies_referenced: list[str],
    citation_tickers: list[str],
    evidence: list[dict[str, Any]],
) -> list[str]:
    """Company symbols the model *claimed* as references but never retrieved.

    Checked against the structured fields only, deliberately — not by scanning the
    prose for upper-case tokens. Analyst writing is dense with acronyms (EPS, EBIT,
    SG&A, DTC, SKU, YoY) and a capitalisation heuristic flags them as invented
    companies, which fires on correct answers and drops their confidence to low. A
    denylist of finance vocabulary only postpones the problem to the next acronym.

    ``companies_referenced`` and ``citations`` are where the model actually asserts
    "this is a company I analysed", so they are the honest place to test the claim.
    A company discussed only in passing prose, with no citation and no reference
    entry, is not being presented as evidence-backed.
    """
    known = _evidence_tickers(evidence)
    claimed = {ticker.strip().upper() for ticker in companies_referenced if ticker.strip()}
    claimed.update(ticker.strip().upper() for ticker in citation_tickers if ticker.strip())
    return sorted(claimed - known - NON_TICKER_TOKENS)


def derive_referenced_companies(
    answer: str, evidence: list[dict[str, Any]]
) -> list[str]:
    """Which retrieved companies the answer actually discusses.

    Structured-output compliance is not guaranteed: models routinely write a correct
    ten-company analysis and return ``companies_referenced: []``. That is not a
    grounding failure, it is a formatting one — and it is fixable without asking the
    model again, because we hold the evidence and can look.

    Matching is exact against the retrieved set (ticker as a whole word, or the
    company's distinctive name token), never a guess at what a symbol might be, so
    this can only ever recover companies that were genuinely retrieved.
    """
    referenced: list[str] = []
    for row in evidence:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker or ticker in referenced:
            continue
        if re.search(rf"\b{re.escape(ticker)}\b", answer):
            referenced.append(ticker)
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        # "Emerson Electric Co." -> "Emerson": the first token is the distinctive one,
        # and suffixes like Inc./Corp./Co. collide across companies.
        head = re.split(r"[\s,.]+", name)[0]
        if len(head) > 3 and re.search(rf"\b{re.escape(head)}\b", answer, re.IGNORECASE):
            referenced.append(ticker)
    return referenced


def backfill_citations(
    referenced: list[str],
    existing: list[Any],
    evidence: list[dict[str, Any]],
    priority_fields: list[str],
) -> list[Any]:
    """Build citations from evidence for referenced companies the model did not cite.

    The evidence panel is the UI's central claim — "every number came from the
    database" — so it must be populated from the data, not from whether the model
    remembered to fill an array.
    """
    from app.agent.schemas import Citation

    by_ticker: dict[str, dict[str, Any]] = {}
    for row in evidence:
        if isinstance(row, dict) and row.get("ticker"):
            by_ticker.setdefault(str(row["ticker"]).upper(), row)

    cited = {citation.ticker.upper() for citation in existing}
    result = list(existing)
    for ticker in referenced:
        if ticker in cited or ticker not in by_ticker:
            continue
        row = by_ticker[ticker]
        fields = [name for name in priority_fields if name in row]
        result.append(
            Citation(
                ticker=ticker,
                company_name=str(row.get("name") or ticker),
                fields_used=fields,
                values={name: row.get(name) for name in fields},
                source=str(row.get("source") or ""),
                as_of=str(row.get("snapshot_date") or ""),
            )
        )
    return result


def compute_confidence(
    evidence: list[dict[str, Any]],
    requested_fields: list[str],
    snapshot_date: str | None,
    out_of_scope: bool,
    today: date | None = None,
) -> tuple[Confidence, str]:
    """Derive confidence from evidence completeness. Precedence is explicit.

    The architecture doc states the thresholds and, separately, that an out-of-scope
    refusal is high confidence, without saying which wins when both apply — a refusal
    retrieves zero companies, so the row-count rule and the refusal rule disagree.
    The refusal rule wins, and the reason says why: we are *certain* we lack the data.
    """
    if out_of_scope:
        return "high", (
            "The dataset was searched and does not contain the company asked about, "
            "so the absence of data is itself a confident finding."
        )

    if not evidence:
        return "low", "No rows were retrieved, so there is nothing to ground an answer in."

    company_count = len(_evidence_tickers(evidence))
    missing = 0
    total = 0
    for row in evidence:
        if not isinstance(row, dict):
            continue
        for name in requested_fields:
            if name in row:
                total += 1
                if row[name] is None:
                    missing += 1

    stale = False
    age_days: int | None = None
    if snapshot_date:
        try:
            snapshot = datetime.strptime(snapshot_date, "%Y-%m-%d").date()
            age_days = ((today or datetime.now(UTC).date()) - snapshot).days
            stale = age_days > STALENESS_DAYS
        except ValueError:
            stale = True

    reasons: list[str] = [f"{company_count} companies retrieved"]
    if total:
        reasons.append(f"{total - missing}/{total} requested field values present")
    if age_days is not None:
        reasons.append(f"snapshot {age_days} days old")

    if company_count >= 3 and missing == 0 and not stale:
        return "high", "; ".join(reasons) + "."
    if company_count == 0:
        return "low", "; ".join(reasons) + "."
    if missing > total * 0.4 or stale:
        return "low", "; ".join(reasons) + "."
    return "medium", "; ".join(reasons) + "."


def check_output(
    draft: AnalystDraft,
    evidence: list[dict[str, Any]],
    requested_fields: list[str],
    snapshot_date: str | None,
    requires_advice_caveat: bool = False,
    today: date | None = None,
) -> OutputGuardResult:
    """Verify a draft against the evidence and compute its confidence."""
    flags: list[str] = []
    caveats: list[str] = list(draft.caveats)

    unverified_tickers = find_unverified_tickers(
        draft.companies_referenced,
        [citation.ticker for citation in draft.citations],
        evidence,
    )
    unverified_numbers = find_unverified_numbers(draft.answer, evidence)

    confidence, reason = compute_confidence(
        evidence=evidence,
        requested_fields=requested_fields,
        snapshot_date=snapshot_date,
        out_of_scope=draft.out_of_scope,
        today=today,
    )

    if unverified_tickers:
        flags.append("ticker_not_in_evidence")
        caveats.append(
            "These symbols appear in the answer but were not returned by any database "
            f"query, so treat them with caution: {', '.join(unverified_tickers)}."
        )
        confidence = "low"

    if unverified_numbers:
        flags.append("number_not_in_evidence")
        caveats.append(
            "These figures could not be traced to a retrieved value and may be "
            f"derived or misstated: {', '.join(unverified_numbers[:5])}."
        )
        if confidence == "high":
            confidence = "medium"

    if requires_advice_caveat:
        flags.append("advice_seeking")
    if NOT_ADVICE_CAVEAT not in caveats:
        caveats.append(NOT_ADVICE_CAVEAT)

    return OutputGuardResult(
        confidence=confidence,
        confidence_reason=reason,
        caveats=caveats,
        flags=flags,
        unverified_tickers=unverified_tickers,
        unverified_numbers=unverified_numbers,
    )
