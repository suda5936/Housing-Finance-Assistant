from copy import deepcopy
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient
from pydantic import ValidationError

from homefit_api.main import app
from homefit_api.policies import EligibilityInput, PolicyDefinition, PolicyRegistry
from homefit_api.rag import (
    EvidenceStatus,
    PolicyEvidenceRegistry,
    PolicySourceCatalog,
    RetrievalEvaluationCase,
    SearchRequest,
    SourceReviewStatus,
    changed_source_document_ids,
    chunk_document,
    evaluate_retrieval,
    ground_eligibility,
)


def _seoul_payload() -> EligibilityInput:
    return EligibilityInput(
        as_of_date=date(2026, 5, 10),
        age_years=25,
        region="서울특별시",
        median_income_ratio_percent="100",
        is_homeless=True,
        deposit="10000000",
        monthly_rent="500000",
        received_same_seoul_support_before=False,
        receiving_other_monthly_rent_support=False,
    )


def _approved_policy(policy: PolicyDefinition) -> PolicyDefinition:
    data = policy.model_dump(mode="json")
    data["source"]["original_sha256"] = "a" * 64
    data["review"] = {
        "status": "approved",
        "author": "rule-author",
        "reviewer": "independent-reviewer",
        "reviewed_at": datetime(2026, 7, 24, tzinfo=UTC).isoformat(),
    }
    return PolicyDefinition.model_validate(data)


def _approved_evidence() -> PolicyEvidenceRegistry:
    source = PolicyEvidenceRegistry.load_default().catalog
    data = source.model_dump(mode="json")
    for document in data["documents"]:
        document["review_status"] = "approved"
        document["reviewer"] = "independent-reviewer"
        document["reviewed_at"] = datetime(2026, 7, 24, tzinfo=UTC).isoformat()
        document["retrieval_status"] = "retrieved"
    return PolicyEvidenceRegistry(PolicySourceCatalog.model_validate(data))


def test_source_catalog_uses_official_domains_and_valid_content_hashes() -> None:
    registry = PolicyEvidenceRegistry.load_default()

    assert len(registry.catalog.documents) == 3
    assert all(document.content_sha256 for document in registry.catalog.documents)
    assert all(document.review_status is SourceReviewStatus.PENDING for document in registry.catalog.documents)
    assert len(registry.chunks) == 9


def test_source_catalog_rejects_non_official_domain_and_tampering() -> None:
    source = PolicyEvidenceRegistry.load_default().catalog
    bad_domain = deepcopy(source.model_dump(mode="json"))
    bad_domain["documents"][0]["url"] = "https://example.com/policy"
    tampered = deepcopy(source.model_dump(mode="json"))
    tampered["documents"][0]["sections"][0]["text"] += "변조"

    for invalid in (bad_domain, tampered):
        try:
            PolicySourceCatalog.model_validate(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("untrusted or tampered evidence must be rejected")


def test_source_approval_requires_an_independent_reviewer() -> None:
    source = PolicyEvidenceRegistry.load_default().catalog
    invalid = source.model_dump(mode="json")
    invalid["documents"][0]["review_status"] = "approved"
    invalid["documents"][0]["reviewer"] = "implementation-draft"
    invalid["documents"][0]["reviewed_at"] = datetime(
        2026, 7, 24, tzinfo=UTC
    ).isoformat()

    try:
        PolicySourceCatalog.model_validate(invalid)
    except ValidationError:
        pass
    else:
        raise AssertionError("self-reviewed evidence must not be approved")


def test_source_change_detection_marks_added_removed_or_revised_documents() -> None:
    previous = PolicyEvidenceRegistry.load_default().catalog
    revised_data = previous.model_dump(mode="json")
    revised_data["documents"][0]["effective_until"] = "2026-11-30"
    revised = PolicySourceCatalog.model_validate(revised_data)

    assert changed_source_document_ids(previous, revised) == [
        "seoul-youth-rent-2026-overview"
    ]


def test_semantic_sections_are_not_split_unnecessarily() -> None:
    document = PolicyEvidenceRegistry.load_default().catalog.documents[0]
    chunks = chunk_document(document)

    assert len(chunks) == len(document.sections)
    assert chunks[0].locator == "사업개요 > 01 지원대상"
    assert "SEOUL_AGE_MAX" in chunks[0].condition_codes


def test_hybrid_search_returns_exact_citation_and_pending_review_status() -> None:
    response = PolicyEvidenceRegistry.load_default().search(
        SearchRequest(
            question="서울 청년 월세 지원금은 얼마인가요?",
            as_of_date=date(2026, 5, 10),
            region="서울특별시",
        )
    )

    assert response.status is EvidenceStatus.OFFICIAL_CHECK_NEEDED
    assert response.reason_codes == ["SOURCE_REVIEW_PENDING"]
    assert response.hits
    assert response.claims[0].citation_ids == [response.hits[0].citation.id]
    assert response.hits[0].citation.quote in response.hits[0].chunk.text
    assert response.hits[0].citation.content_sha256
    assert response.hits[0].hybrid_score > 0


def test_approved_source_can_return_supported_evidence() -> None:
    response = _approved_evidence().search(
        SearchRequest(
            question="청년 월세 지원내용",
            as_of_date=date(2026, 5, 10),
            region="서울특별시",
            policy_codes=["seoul_youth_monthly_rent_2026"],
        )
    )

    assert response.status is EvidenceStatus.EVIDENCE_FOUND
    assert response.reason_codes == ["OFFICIAL_EVIDENCE_FOUND"]


def test_effective_date_region_and_policy_filters_prevent_wrong_evidence() -> None:
    registry = PolicyEvidenceRegistry.load_default()
    expired = registry.search(
        SearchRequest(
            question="서울 청년 월세 지원",
            as_of_date=date(2027, 1, 1),
            region="서울특별시",
            policy_codes=["seoul_youth_monthly_rent_2026"],
        )
    )
    wrong_region = registry.search(
        SearchRequest(
            question="서울 청년 월세 지원",
            as_of_date=date(2026, 5, 10),
            region="부산광역시",
            policy_codes=["seoul_youth_monthly_rent_2026"],
        )
    )

    assert expired.hits == []
    assert wrong_region.hits == []
    assert expired.reason_codes == ["NO_APPLICABLE_OFFICIAL_EVIDENCE"]


def test_unrelated_query_does_not_generate_an_answer() -> None:
    response = PolicyEvidenceRegistry.load_default().search(
        SearchRequest(question="화성 탐사 로켓 연료", as_of_date=date(2026, 7, 24))
    )

    assert response.status is EvidenceStatus.OFFICIAL_CHECK_NEEDED
    assert response.claims == []
    assert response.hits == []


def test_every_approved_seoul_rule_condition_has_citation_but_pending_source_blocks() -> None:
    policy = _approved_policy(
        PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026")
    )
    result = ground_eligibility(
        policy,
        _seoul_payload(),
        PolicyEvidenceRegistry.load_default(),
    )

    assert result.evidence_status is EvidenceStatus.OFFICIAL_CHECK_NEEDED
    assert "SOURCE_OR_RULE_REVIEW_PENDING" in result.reason_codes
    assert len(result.grounded_conditions) == len(policy.conditions)
    assert all(item.citations for item in result.grounded_conditions)


def test_approved_rules_and_sources_ground_every_condition() -> None:
    policy = _approved_policy(
        PolicyRegistry.load_default().get("seoul_youth_monthly_rent_2026")
    )
    result = ground_eligibility(policy, _seoul_payload(), _approved_evidence())

    assert result.evidence_status is EvidenceStatus.EVIDENCE_FOUND
    assert result.reason_codes == ["ALL_CONDITIONS_GROUNDED"]
    assert all(item.status is EvidenceStatus.EVIDENCE_FOUND for item in result.grounded_conditions)


def test_retrieval_evaluation_measures_hits_and_citation_alignment_separately() -> None:
    cases = [
        RetrievalEvaluationCase(
            question="서울 청년월세 연령은 몇 살인가요?",
            as_of_date=date(2026, 5, 10),
            region="서울특별시",
            expected_document_id="seoul-youth-rent-2026-overview",
            expected_condition_code="SEOUL_AGE_MAX",
        ),
        RetrievalEvaluationCase(
            question="청년 보증부월세 대출은 몇 살까지 가능한가요?",
            as_of_date=date(2026, 7, 24),
            expected_document_id="nhuf-youth-deposit-monthly-loan",
            expected_condition_code="YOUTH_LOAN_AGE_MAX",
        ),
        RetrievalEvaluationCase(
            question="주거안정월세대출 대상주택 면적은?",
            as_of_date=date(2026, 7, 24),
            expected_document_id="nhuf-housing-stability-monthly-loan",
            expected_condition_code="STABILITY_LOAN_AREA",
        ),
    ]

    evaluation = evaluate_retrieval(PolicyEvidenceRegistry.load_default(), cases)

    assert evaluation.total_cases == 3
    assert evaluation.hit_rate_at_k == 1
    assert evaluation.citation_alignment == 1


def test_evidence_api_searches_and_returns_grounded_safety_gate() -> None:
    client = TestClient(app)

    searched = client.post(
        "/policy-evidence/search",
        json={
            "question": "서울 청년 월세 지원금",
            "as_of_date": "2026-05-10",
            "region": "서울특별시",
        },
    )
    grounded = client.post(
        "/policies/seoul_youth_monthly_rent_2026/eligibility-with-evidence",
        json=_seoul_payload().model_dump(mode="json"),
    )
    missing = client.post(
        "/policies/not-registered/eligibility-with-evidence",
        json=_seoul_payload().model_dump(mode="json"),
    )

    assert searched.status_code == 200
    assert searched.json()["hits"][0]["citation"]["url"].startswith("https://housing.seoul.go.kr")
    assert grounded.status_code == 200
    assert grounded.json()["eligibility"]["status"] == "OFFICIAL_CHECK_NEEDED"
    assert grounded.json()["grounded_conditions"] == []
    assert missing.status_code == 404
