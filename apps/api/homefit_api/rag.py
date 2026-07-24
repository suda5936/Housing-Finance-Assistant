import hashlib
import json
import math
import re
from collections import Counter
from datetime import date, datetime
from enum import StrEnum
from importlib.resources import files
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl, model_validator

from homefit_api.policies import (
    EligibilityInput,
    EligibilityResult,
    PolicyDefinition,
    PolicyRegistry,
    evaluate_policy,
)

OFFICIAL_DOMAINS = frozenset({"housing.seoul.go.kr", "nhuf.molit.go.kr"})
TOKEN_PATTERN = re.compile(r"[가-힣a-z0-9]+")
SENTENCE_PATTERN = re.compile(r"[^.!?。]+[.!?。]?")
VECTOR_DIMENSIONS = 192
Score = Annotated[float, Field(ge=0, le=1)]


class SourceReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    RETIRED = "retired"


class RetrievalStatus(StrEnum):
    RETRIEVED = "retrieved"
    MANUAL_SNAPSHOT = "manual_snapshot"
    UNAVAILABLE = "unavailable"


class EvidenceStatus(StrEnum):
    EVIDENCE_FOUND = "EVIDENCE_FOUND"
    OFFICIAL_CHECK_NEEDED = "OFFICIAL_CHECK_NEEDED"


class SourceSection(BaseModel):
    heading: str = Field(min_length=1, max_length=200)
    locator: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=10_000)
    condition_codes: list[str] = Field(default_factory=list)


class PolicySourceDocument(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=100)
    policy_code: str = Field(pattern=r"^[a-z0-9_]+$", max_length=80)
    title: str = Field(min_length=1, max_length=200)
    institution: str = Field(min_length=1, max_length=150)
    url: HttpUrl
    source_type: str = Field(pattern=r"^official_[a-z_]+$", max_length=50)
    published_on: date | None = None
    checked_on: date
    effective_from: date
    effective_until: date | None = None
    regions: list[str] = Field(min_length=1)
    review_status: SourceReviewStatus
    author: str = Field(min_length=1, max_length=100)
    reviewer: str | None = Field(default=None, max_length=100)
    reviewed_at: datetime | None = None
    retrieval_status: RetrievalStatus
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sections: list[SourceSection] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source(self) -> "PolicySourceDocument":
        hostname = (urlparse(str(self.url)).hostname or "").lower()
        if hostname not in OFFICIAL_DOMAINS:
            raise ValueError("policy evidence must use an allow-listed official domain")
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("effective_until must not precede effective_from")
        if self.review_status is SourceReviewStatus.APPROVED:
            if not self.reviewer or self.reviewed_at is None:
                raise ValueError("approved evidence requires reviewer and reviewed_at")
            if self.author.strip().casefold() == self.reviewer.strip().casefold():
                raise ValueError("evidence author and reviewer must be different people")
        canonical = json.dumps(
            [section.model_dump(mode="json") for section in self.sections],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if digest != self.content_sha256:
            raise ValueError("policy source content hash does not match")
        return self

    @property
    def requires_official_check(self) -> bool:
        return (
            self.review_status is not SourceReviewStatus.APPROVED
            or self.retrieval_status is not RetrievalStatus.RETRIEVED
        )


class PolicySourceCatalog(BaseModel):
    schema_version: str = Field(min_length=1, max_length=30)
    documents: list[PolicySourceDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_documents(self) -> "PolicySourceCatalog":
        ids = [document.id for document in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("policy source document ids must be unique")
        return self


class EvidenceChunk(BaseModel):
    id: str
    document_id: str
    policy_code: str
    section_heading: str
    locator: str
    text: str
    condition_codes: list[str]
    regions: list[str]
    effective_from: date
    effective_until: date | None


class Citation(BaseModel):
    id: str
    chunk_id: str
    document_id: str
    policy_code: str
    title: str
    institution: str
    url: HttpUrl
    locator: str
    quote: str
    checked_on: date
    content_sha256: str
    review_status: SourceReviewStatus
    reviewer: str | None
    reviewed_at: datetime | None
    retrieval_status: RetrievalStatus


class SearchRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    as_of_date: date
    region: str | None = Field(default=None, max_length=50)
    policy_codes: list[str] = Field(default_factory=list, max_length=10)
    top_k: int = Field(default=3, ge=1, le=10)


class SearchHit(BaseModel):
    chunk: EvidenceChunk
    keyword_score: Score
    vector_score: Score
    hybrid_score: Score
    citation: Citation
    requires_official_check: bool


class GroundedClaim(BaseModel):
    sentence: str = Field(min_length=1, max_length=1000)
    citation_ids: list[str] = Field(min_length=1)
    condition_code: str | None = None


class SearchResponse(BaseModel):
    status: EvidenceStatus
    reason_codes: list[str]
    query: SearchRequest
    claims: list[GroundedClaim]
    hits: list[SearchHit]


class GroundedCondition(BaseModel):
    condition_code: str
    outcome: bool | None
    explanation: str
    citations: list[Citation]
    status: EvidenceStatus


class GroundedEligibilityResult(BaseModel):
    eligibility: EligibilityResult
    evidence_status: EvidenceStatus
    reason_codes: list[str]
    grounded_conditions: list[GroundedCondition]


class RetrievalEvaluationCase(BaseModel):
    question: str
    as_of_date: date
    region: str | None = None
    expected_document_id: str
    expected_condition_code: str | None = None


class RetrievalEvaluation(BaseModel):
    total_cases: int
    hits_at_k: int
    hit_rate_at_k: Score
    citation_matches: int
    citation_alignment: Score


def _tokenize(text: str) -> list[str]:
    normalized = text.casefold().replace("㎡", " 제곱미터 ")
    words = TOKEN_PATTERN.findall(normalized)
    tokens = list(words)
    for word in words:
        if re.fullmatch(r"[가-힣]+", word) and len(word) >= 3:
            tokens.extend(word[index : index + 2] for index in range(len(word) - 1))
    return tokens


def _vector(tokens: list[str]) -> list[float]:
    values = [0.0] * VECTOR_DIMENSIONS
    for token, count in Counter(tokens).items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest, "big") % VECTOR_DIMENSIONS
        values[index] += 1.0 + math.log(count)
    norm = math.sqrt(sum(value * value for value in values))
    return values if norm == 0 else [value / norm for value in values]


def _cosine(left: list[float], right: list[float]) -> float:
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True))))


def _keyword_score(query_tokens: list[str], document_tokens: list[str]) -> float:
    if not query_tokens or not document_tokens:
        return 0.0
    query_counts = Counter(query_tokens)
    document_counts = Counter(document_tokens)
    matched = sum(min(count, document_counts[token]) for token, count in query_counts.items())
    return min(1.0, matched / sum(query_counts.values()))


def _best_sentence(text: str, question: str) -> str:
    query_tokens = _tokenize(question)
    sentences = [match.group(0).strip() for match in SENTENCE_PATTERN.finditer(text)]
    if not sentences:
        return text
    return max(sentences, key=lambda sentence: _keyword_score(query_tokens, _tokenize(sentence)))


def chunk_document(document: PolicySourceDocument, max_chars: int = 800) -> list[EvidenceChunk]:
    chunks: list[EvidenceChunk] = []
    for section_index, section in enumerate(document.sections, start=1):
        paragraphs = [part.strip() for part in section.text.split("\n\n") if part.strip()]
        groups: list[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip()
            if current and len(candidate) > max_chars:
                groups.append(current)
                current = paragraph
            else:
                current = candidate
        if current:
            groups.append(current)
        for group_index, text in enumerate(groups, start=1):
            chunks.append(
                EvidenceChunk(
                    id=f"{document.id}:s{section_index}:c{group_index}",
                    document_id=document.id,
                    policy_code=document.policy_code,
                    section_heading=section.heading,
                    locator=section.locator,
                    text=text,
                    condition_codes=section.condition_codes,
                    regions=document.regions,
                    effective_from=document.effective_from,
                    effective_until=document.effective_until,
                )
            )
    return chunks


class PolicyEvidenceRegistry:
    def __init__(self, catalog: PolicySourceCatalog) -> None:
        self.catalog = catalog
        self.documents = {document.id: document for document in catalog.documents}
        self.chunks = [
            chunk for document in catalog.documents for chunk in chunk_document(document)
        ]
        self._chunk_vectors = {
            chunk.id: _vector(_tokenize(self._searchable_text(chunk)))
            for chunk in self.chunks
        }

    @classmethod
    def load_default(cls) -> "PolicyEvidenceRegistry":
        resource = files("homefit_api.policy_data").joinpath("policy_sources.json")
        catalog = PolicySourceCatalog.model_validate_json(resource.read_text(encoding="utf-8"))
        return cls(catalog)

    def search(self, request: SearchRequest) -> SearchResponse:
        query_tokens = _tokenize(request.question)
        query_vector = _vector(query_tokens)
        requested_codes = set(request.policy_codes)
        scored: list[tuple[float, float, float, EvidenceChunk]] = []
        for chunk in self.chunks:
            if requested_codes and chunk.policy_code not in requested_codes:
                continue
            if request.region and "전국" not in chunk.regions and request.region not in chunk.regions:
                continue
            if request.as_of_date < chunk.effective_from:
                continue
            if chunk.effective_until and request.as_of_date > chunk.effective_until:
                continue
            searchable_text = self._searchable_text(chunk)
            keyword = _keyword_score(query_tokens, _tokenize(searchable_text))
            vector = _cosine(query_vector, self._chunk_vectors[chunk.id])
            region_boost = (
                0.05
                if request.region and request.region in chunk.regions and "전국" not in chunk.regions
                else 0.0
            )
            hybrid = min(1.0, keyword * 0.65 + vector * 0.35 + region_boost)
            # The local hash vector only reranks lexical candidates. It must not
            # manufacture a match for a query with no shared policy terminology.
            if keyword > 0:
                scored.append((hybrid, keyword, vector, chunk))
        scored.sort(key=lambda item: (-item[0], item[3].id))
        hits = [self._hit(chunk, request.question, keyword, vector, hybrid) for hybrid, keyword, vector, chunk in scored[: request.top_k]]
        if not hits:
            return SearchResponse(
                status=EvidenceStatus.OFFICIAL_CHECK_NEEDED,
                reason_codes=["NO_APPLICABLE_OFFICIAL_EVIDENCE"],
                query=request,
                claims=[],
                hits=[],
            )
        requires_check = any(hit.requires_official_check for hit in hits)
        claims = [
            GroundedClaim(
                sentence=hit.citation.quote,
                citation_ids=[hit.citation.id],
                condition_code=None,
            )
            for hit in hits
        ]
        return SearchResponse(
            status=(
                EvidenceStatus.OFFICIAL_CHECK_NEEDED
                if requires_check
                else EvidenceStatus.EVIDENCE_FOUND
            ),
            reason_codes=["SOURCE_REVIEW_PENDING"] if requires_check else ["OFFICIAL_EVIDENCE_FOUND"],
            query=request,
            claims=claims,
            hits=hits,
        )

    def _searchable_text(self, chunk: EvidenceChunk) -> str:
        document = self.documents[chunk.document_id]
        return (
            f"{document.title} {document.institution} "
            f"{chunk.section_heading} {chunk.text}"
        )

    def citations_for_condition(
        self,
        policy_code: str,
        condition_code: str,
        as_of_date: date,
    ) -> list[Citation]:
        matches = [
            chunk
            for chunk in self.chunks
            if chunk.policy_code == policy_code
            and condition_code in chunk.condition_codes
            and as_of_date >= chunk.effective_from
            and (chunk.effective_until is None or as_of_date <= chunk.effective_until)
        ]
        return [self._citation(chunk, chunk.text) for chunk in matches]

    def _hit(
        self,
        chunk: EvidenceChunk,
        question: str,
        keyword_score: float,
        vector_score: float,
        hybrid_score: float,
    ) -> SearchHit:
        quote = _best_sentence(chunk.text, question)
        document = self.documents[chunk.document_id]
        return SearchHit(
            chunk=chunk,
            keyword_score=round(keyword_score, 6),
            vector_score=round(vector_score, 6),
            hybrid_score=round(hybrid_score, 6),
            citation=self._citation(chunk, quote),
            requires_official_check=document.requires_official_check,
        )

    def _citation(self, chunk: EvidenceChunk, quote: str) -> Citation:
        document = self.documents[chunk.document_id]
        citation_id = hashlib.sha256(f"{chunk.id}:{quote}".encode()).hexdigest()[:16]
        return Citation(
            id=citation_id,
            chunk_id=chunk.id,
            document_id=document.id,
            policy_code=document.policy_code,
            title=document.title,
            institution=document.institution,
            url=document.url,
            locator=chunk.locator,
            quote=quote,
            checked_on=document.checked_on,
            content_sha256=document.content_sha256,
            review_status=document.review_status,
            reviewer=document.reviewer,
            reviewed_at=document.reviewed_at,
            retrieval_status=document.retrieval_status,
        )


def ground_eligibility(
    policy: PolicyDefinition,
    payload: EligibilityInput,
    evidence: PolicyEvidenceRegistry,
) -> GroundedEligibilityResult:
    result = evaluate_policy(policy, payload)
    explanations = {condition.code: condition.explanation for condition in policy.conditions}
    grounded: list[GroundedCondition] = []
    missing_evidence = False
    pending_review = False
    for check in result.checks:
        citations = evidence.citations_for_condition(
            policy.code, check.code, payload.as_of_date
        )
        if not citations:
            missing_evidence = True
        citation_pending = any(
            citation.review_status is not SourceReviewStatus.APPROVED
            or citation.retrieval_status is not RetrievalStatus.RETRIEVED
            for citation in citations
        )
        if citation_pending:
            pending_review = True
        grounded.append(
            GroundedCondition(
                condition_code=check.code,
                outcome=check.passed,
                explanation=explanations[check.code],
                citations=citations,
                status=(
                    EvidenceStatus.EVIDENCE_FOUND
                    if citations and not citation_pending
                    else EvidenceStatus.OFFICIAL_CHECK_NEEDED
                ),
            )
        )
    reason_codes: list[str] = []
    if missing_evidence:
        reason_codes.append("CONDITION_EVIDENCE_MISSING")
    if pending_review or not policy.is_activatable:
        reason_codes.append("SOURCE_OR_RULE_REVIEW_PENDING")
    if not grounded:
        reason_codes.append("NO_EVALUATED_CONDITIONS")
    status = (
        EvidenceStatus.OFFICIAL_CHECK_NEEDED
        if reason_codes
        else EvidenceStatus.EVIDENCE_FOUND
    )
    return GroundedEligibilityResult(
        eligibility=result,
        evidence_status=status,
        reason_codes=reason_codes or ["ALL_CONDITIONS_GROUNDED"],
        grounded_conditions=grounded,
    )


def evaluate_retrieval(
    registry: PolicyEvidenceRegistry,
    cases: list[RetrievalEvaluationCase],
    top_k: int = 3,
) -> RetrievalEvaluation:
    hits = 0
    aligned = 0
    for case in cases:
        response = registry.search(
            SearchRequest(
                question=case.question,
                as_of_date=case.as_of_date,
                region=case.region,
                top_k=top_k,
            )
        )
        matching_hits = [
            hit for hit in response.hits if hit.chunk.document_id == case.expected_document_id
        ]
        if matching_hits:
            hits += 1
        if case.expected_condition_code is None:
            aligned += int(bool(matching_hits))
        elif any(
            case.expected_condition_code in hit.chunk.condition_codes for hit in matching_hits
        ):
            aligned += 1
    total = len(cases)
    return RetrievalEvaluation(
        total_cases=total,
        hits_at_k=hits,
        hit_rate_at_k=0 if total == 0 else hits / total,
        citation_matches=aligned,
        citation_alignment=0 if total == 0 else aligned / total,
    )


def changed_source_document_ids(
    previous: PolicySourceCatalog,
    current: PolicySourceCatalog,
) -> list[str]:
    """Identify sources that must be re-chunked and independently reviewed."""

    old_versions = {
        document.id: (
            document.content_sha256,
            document.effective_from,
            document.effective_until,
            str(document.url),
        )
        for document in previous.documents
    }
    new_versions = {
        document.id: (
            document.content_sha256,
            document.effective_from,
            document.effective_until,
            str(document.url),
        )
        for document in current.documents
    }
    return sorted(
        document_id
        for document_id in old_versions.keys() | new_versions.keys()
        if old_versions.get(document_id) != new_versions.get(document_id)
    )


def build_default_grounded_result(
    policy_registry: PolicyRegistry,
    evidence_registry: PolicyEvidenceRegistry,
    policy_code: str,
    payload: EligibilityInput,
) -> GroundedEligibilityResult:
    return ground_eligibility(policy_registry.get(policy_code), payload, evidence_registry)
