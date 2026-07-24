import pytest
from pydantic import ValidationError

from homefit_api.evaluation import ManualGate, evaluate_release, load_release_dataset


def test_release_evaluation_passes_all_automated_blocking_checks() -> None:
    report = evaluate_release()

    assert report.dataset_version == "phase11-release-v1"
    assert report.automated_gate_passed is True
    assert report.automated_blockers == []
    assert all(metric.accuracy == 1 for metric in report.metrics)
    assert {metric.area for metric in report.metrics} == {
        "cost",
        "document_safety",
        "policy",
        "ranking",
        "retrieval",
    }


def test_release_readiness_keeps_human_evidence_separate() -> None:
    report = evaluate_release()

    assert report.release_ready is False
    assert {gate.id for gate in report.pending_manual_gates} == {
        "manual-mobile-390px-keyboard",
        "manual-policy-independent-review",
    }


def test_release_evaluation_is_reproducible() -> None:
    assert evaluate_release() == evaluate_release()


def test_changed_golden_value_becomes_a_release_blocker() -> None:
    dataset = load_release_dataset().model_copy(deep=True)
    dataset.cost_cases[0].expected["contract_total_cost"] = "0"

    report = evaluate_release(dataset)

    assert report.automated_gate_passed is False
    assert report.automated_blockers == [
        "cost-hand-calculated-base:contract_total_cost"
    ]


def test_manual_gate_cannot_be_completed_without_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence reference"):
        ManualGate(id="manual-test", description="human check", completed=True)
