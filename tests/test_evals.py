"""Tests for the evaluation scorers.

The scorers decide whether the build ships, so they get the same scrutiny as the
code they grade. Everything here is synthetic — no LLM, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.personas import PERSONA_KEYS
from app.agent.sectors import SECTOR_KEYS
from evals.run_eval import (
    GATES,
    CaseResult,
    conclusion_divergence,
    gate_status,
    lexical_divergence,
    load_cases,
    membership_divergence,
    persona_divergence,
    summarise,
    verdict_probe,
)

DATASET = Path(__file__).parent.parent / "evals" / "dataset.jsonl"


def case(**overrides: object) -> CaseResult:
    base: dict[str, object] = {
        "id": "x",
        "category": "cross_persona",
        "persona": "mf_analyst",
        "sector": "tech",
        "query": "q",
        "passed": True,
        "detail": "",
        "answer": "",
        "companies": [],
        "tools_called": ["query_companies"],
        "divergence_group": "tech-deploy",
    }
    base.update(overrides)
    return CaseResult(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Dataset integrity
# --------------------------------------------------------------------------


def test_dataset_parses_and_ids_are_unique() -> None:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 25
    assert len(rows) == len({row["id"] for row in rows})


def test_every_case_names_a_real_persona_and_sector() -> None:
    for row in load_cases(None):
        assert row["persona"] in PERSONA_KEYS, row["id"]
        assert row["sector"] in SECTOR_KEYS, row["id"]


def test_all_required_categories_are_covered() -> None:
    categories = {row["category"] for row in load_cases(None)}
    assert {
        "cross_persona",
        "persona_specific",
        "grounding",
        "out_of_scope",
        "adversarial",
        "api_contract",
        "divergence_probe",
    } <= categories


def test_the_briefs_own_example_is_in_the_set() -> None:
    """PE Analyst x Logistics, the assessment's worked example."""
    rows = load_cases(None)
    assert any(r["persona"] == "pe_analyst" and r["sector"] == "logistics" for r in rows)


# --------------------------------------------------------------------------
# Divergence — the metric the headline claim rests on
# --------------------------------------------------------------------------


def test_identical_answers_score_near_zero_divergence() -> None:
    """A metric that rewards identical answers would prove nothing."""
    same = [
        case(persona=p, answer="The sector looks strong on growth and margins.", companies=["NVDA", "MSFT"])
        for p in PERSONA_KEYS
    ]
    score, _ = persona_divergence(same)
    assert score < 0.10


def test_genuinely_different_reasoning_scores_high() -> None:
    different = [
        case(persona="mf_analyst", answer="Benchmark-relative, durable growth, core holding, low beta portfolio fit.", companies=["MSFT", "AAPL"]),
        case(persona="equity_analyst", answer="Margin structure, earnings quality, return on equity, multiple defensible.", companies=["GOOGL", "NVDA"]),
        case(persona="pe_analyst", answer="Entry multiple, leverage headroom, free cash flow, operational levers, exit.", companies=["ADBE", "ORCL"]),
    ]
    score, components = persona_divergence(different)
    assert score >= GATES["persona_divergence"]
    assert components["conclusion_divergence"] == pytest.approx(1.0)


def test_conclusions_dominate_the_weighting() -> None:
    """Vocabulary alone must not clear the gate.

    Three answers stuffed with distinct lens keywords but picking the *same*
    companies is exactly the cosmetic tone change the brief says does not count, so it
    has to score below the bar.
    """
    vocabulary_only = [
        case(persona="mf_analyst", answer="Benchmark portfolio durable volatility core holding total return peer.", companies=["NVDA", "MSFT", "AAPL"]),
        case(persona="equity_analyst", answer="Margin earnings multiple valuation return on equity cost structure under pressure.", companies=["NVDA", "MSFT", "AAPL"]),
        case(persona="pe_analyst", answer="Leverage ebitda entry multiple exit free cash flow operational deal.", companies=["NVDA", "MSFT", "AAPL"]),
    ]
    score, components = persona_divergence(vocabulary_only)
    assert components["conclusion_divergence"] == pytest.approx(0.0)
    assert score < GATES["persona_divergence"], (
        "identical company picks cleared the divergence gate on wording alone"
    )


def test_conclusion_divergence_measures_company_picks() -> None:
    assert conclusion_divergence([case(companies=["A", "B"]), case(companies=["A", "B"])]) == 0.0
    assert conclusion_divergence([case(companies=["A"]), case(companies=["B"])]) == 1.0


def test_lexical_divergence_bounds() -> None:
    assert lexical_divergence(["same words here", "same words here"]) == pytest.approx(0.0)
    assert lexical_divergence(["alpha bravo charlie", "delta echo foxtrot"]) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# The verdict probe — not gameable by vocabulary
# --------------------------------------------------------------------------


def test_probe_rewards_an_actual_inversion() -> None:
    probes = [
        case(category="divergence_probe", persona="mf_analyst", expected_verdict="negative",
             answer="This weak margin is a defect and a reason to avoid the name."),
        case(category="divergence_probe", persona="pe_analyst", expected_verdict="positive",
             answer="That weak margin is an operational lever and a clear opportunity to improve."),
    ]
    score, detail = verdict_probe(probes)
    assert score == 1.0
    assert "inverts" in detail and "does NOT" not in detail


def test_probe_penalises_agreement() -> None:
    """If both lenses reach the same conclusion, the headline claim is false."""
    probes = [
        case(category="divergence_probe", persona="mf_analyst", expected_verdict="negative",
             answer="A clear opportunity and lever to improve, attractive."),
        case(category="divergence_probe", persona="pe_analyst", expected_verdict="positive",
             answer="A clear opportunity and lever to improve, attractive."),
    ]
    score, detail = verdict_probe(probes)
    assert score < 1.0
    assert "does NOT invert" in detail


def test_probe_reports_honestly_when_not_run() -> None:
    score, detail = verdict_probe([case()])
    assert score == 0.0
    assert "not run" in detail


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------


def test_refusal_gate_is_all_or_nothing() -> None:
    """The brief's honesty test is the one metric with no tolerance."""
    assert GATES["refusal_accuracy"] == 1.00


def test_a_single_failed_refusal_fails_the_gate() -> None:
    results = [
        case(category="out_of_scope", passed=True, answer="I have no data on X."),
        case(category="out_of_scope", passed=False, answer="X looks attractive."),
    ]
    summary = summarise(results)
    assert summary["refusal_accuracy"] == 0.5
    rows = {row[0]: row[3] for row in gate_status(summary)}
    assert rows["refusal_accuracy"] is False


def test_zero_tool_call_gate_is_inverted() -> None:
    """Lower is better for this one; the comparison must not be flipped."""
    clean = summarise([case(answer="grounded", tools_called=["query_companies"])])
    assert clean["zero_tool_call_rate"] == 0.0
    assert {r[0]: r[3] for r in gate_status(clean)}["zero_tool_call_rate"] is True

    ungrounded = summarise([case(answer="from memory", tools_called=[])])
    assert ungrounded["zero_tool_call_rate"] == 1.0
    assert {r[0]: r[3] for r in gate_status(ungrounded)}["zero_tool_call_rate"] is False


def test_blocked_queries_do_not_count_as_zero_tool_call_answers() -> None:
    """An injection blocked before the LLM legitimately makes no tool calls."""
    results = [
        case(answer="refused", tools_called=[], guard_flags=["injection"]),
        case(answer="grounded", tools_called=["query_companies"]),
    ]
    assert summarise(results)["zero_tool_call_rate"] == 0.0


def test_groundedness_counts_untraceable_figures() -> None:
    results = [
        case(answer="Margin is 18.75%.", unverified_numbers=[]),
        case(answer="Revenue was $87.4B.", unverified_numbers=["87.4"]),
    ]
    assert summarise(results)["groundedness"] == 0.5


def test_membership_is_uninformative_when_the_question_invites_a_survey() -> None:
    """Real data from a run: every lens named all ten tech companies.

    Scoring conclusions on raw membership would report ~0.07 divergence for three
    analyses that reached visibly different recommendations — understating the very
    thing the metric exists to measure.
    """
    observed = [
        case(persona="mf_analyst", answer="a",
             companies=["NVDA", "GOOGL", "MSFT", "META", "AMD", "AAPL", "ADBE", "CRM", "ORCL", "INTC"]),
        case(persona="equity_analyst", answer="a",
             companies=["NVDA", "AAPL", "GOOGL", "MSFT", "META", "AMD", "INTC", "ORCL", "CRM", "ADBE"]),
        case(persona="pe_analyst", answer="a",
             companies=["ADBE", "META", "CRM", "ORCL", "NVDA", "AAPL", "GOOGL", "MSFT", "AMD"]),
    ]
    assert membership_divergence(observed) < 0.15
    # The buyout lens led with ADBE/META/CRM while the others led with NVDA/GOOGL/MSFT.
    assert conclusion_divergence(observed) > 0.60


def test_leading_with_the_same_names_is_still_no_divergence() -> None:
    """The top-K measure must not be a free pass — same leaders, same conclusion."""
    identical = [
        case(persona=p, answer="a", companies=["NVDA", "MSFT", "AAPL", "GOOGL"])
        for p in PERSONA_KEYS
    ]
    assert conclusion_divergence(identical) == 0.0
