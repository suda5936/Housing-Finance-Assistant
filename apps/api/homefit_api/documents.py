import csv
import hashlib
import importlib
import io
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from homefit_api.data import DataRepository, SourceDocument
from homefit_api.settings import Settings

EXTRACTION_VERSION = "document-extraction-v1"
ALLOWED_MEDIA_TYPES = {"application/pdf", "image/png", "image/jpeg"}
EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
REQUIRED_FIELDS = {
    "address",
    "deposit",
    "monthly_rent",
    "maintenance_fee",
    "area_sqm",
    "contract_period",
}
INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"이전\s*(지시|명령).{0,10}무시"),
    re.compile(r"AI.{0,10}(지시|명령|실행)"),
)
PII_PATTERNS = (
    re.compile(r"\b\d{6}-?[1-4]\d{6}\b"),
    re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b\d{2,4}-\d{2,6}-\d{4,8}\b"),
)


class DocumentStatus(StrEnum):
    STORED = "stored"
    EXTRACTED = "extracted"
    MANUAL_REQUIRED = "manual_required"
    FAILED = "failed"


class FieldStatus(StrEnum):
    PROPOSED = "proposed"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"


class DocumentFieldName(StrEnum):
    ADDRESS = "address"
    DEPOSIT = "deposit"
    MONTHLY_RENT = "monthly_rent"
    MAINTENANCE_FEE = "maintenance_fee"
    MAINTENANCE_INCLUDED_ITEMS = "maintenance_included_items"
    AREA_SQM = "area_sqm"
    AREA_ORIGINAL = "area_original"
    CONTRACT_PERIOD = "contract_period"
    SPECIAL_TERMS = "special_terms"
    BROKERAGE_FEE = "brokerage_fee"


class BoundingBox(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)


class TextBlock(BaseModel):
    id: str
    page: int = Field(ge=1)
    text: str
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    bounding_box: BoundingBox | None = None


class ExtractedFieldValue(BaseModel):
    id: UUID
    name: DocumentFieldName
    raw_text: str
    normalized_value: str
    unit: str | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    source_block_ids: list[str]
    status: FieldStatus
    confirmed_value: str | None = None
    confirmed_at: datetime | None = None


class UploadedDocument(BaseModel):
    id: UUID
    session_id: UUID
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    page_count: int | None
    image_width: int | None
    image_height: int | None
    status: DocumentStatus
    extractor: str | None
    extraction_version: str | None
    warnings: list[str]
    manual_entry_available: bool = True
    expires_at: datetime
    created_at: datetime


class DocumentAnalysis(BaseModel):
    document: UploadedDocument
    masked_text: str
    blocks: list[TextBlock]
    fields: list[ExtractedFieldValue]
    missing_required_fields: list[str]
    contradictions: list[str]
    injection_detected: bool


class FieldReviewInput(BaseModel):
    value: str = Field(min_length=1, max_length=2000)
    confirmed: bool = True


class ConfirmedDocumentFields(BaseModel):
    document_id: UUID
    values: dict[str, str]
    missing_confirmed_fields: list[str]
    ready_for_calculation: bool


class FieldAccuracy(BaseModel):
    field_name: DocumentFieldName
    expected_count: int
    exact_match_count: int
    exact_match_accuracy: Decimal = Field(ge=0, le=1)


class ExtractionEvaluation(BaseModel):
    document_count: int
    fields: list[FieldAccuracy]
    macro_accuracy: Decimal = Field(ge=0, le=1)


class ExtractionOutput(BaseModel):
    engine: str
    version: str
    blocks: list[TextBlock]
    warnings: list[str] = Field(default_factory=list)


class UploadValidationError(ValueError):
    pass


class DocumentNotFoundError(LookupError):
    pass


class ExtractionUnavailableError(RuntimeError):
    pass


class DocumentTextExtractor(Protocol):
    def extract(self, path: Path, media_type: str) -> ExtractionOutput: ...


def mask_sensitive_text(text: str) -> str:
    masked = text
    for pattern in PII_PATTERNS:
        masked = pattern.sub("[REDACTED]", masked)
    return masked


def contains_prompt_injection(text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in INJECTION_PATTERNS)


def _sniff_media_type(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _png_dimensions(content: bytes) -> tuple[int, int]:
    if len(content) < 24:
        raise UploadValidationError("PNG header is incomplete")
    return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    position = 2
    while position + 9 < len(content):
        if content[position] != 0xFF:
            position += 1
            continue
        marker = content[position + 1]
        position += 2
        if marker in {0xD8, 0xD9}:
            continue
        if position + 2 > len(content):
            break
        length = int.from_bytes(content[position : position + 2], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB}:
            if position + 7 > len(content):
                break
            height = int.from_bytes(content[position + 3 : position + 5], "big")
            width = int.from_bytes(content[position + 5 : position + 7], "big")
            return width, height
        if length < 2:
            break
        position += length
    raise UploadValidationError("JPEG dimensions could not be read")


def _validate_pdf(content: bytes, max_pages: int) -> int:
    dangerous_tokens = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction")
    if any(token in content for token in dangerous_tokens):
        raise UploadValidationError("PDF contains an unsupported active or embedded element")
    page_count = len(re.findall(rb"/Type\s*/Page\b", content))
    if page_count == 0:
        raise UploadValidationError("PDF page structure could not be verified")
    if page_count > max_pages:
        raise UploadValidationError(f"PDF exceeds the {max_pages}-page limit")
    return page_count


def validate_upload(
    *,
    filename: str,
    declared_media_type: str | None,
    content: bytes,
    settings: Settings,
) -> tuple[str, int | None, int | None, int | None]:
    if not filename or Path(filename).name != filename:
        raise UploadValidationError("Filename must not contain a path")
    if len(content) == 0:
        raise UploadValidationError("File is empty")
    if len(content) > settings.document_max_bytes:
        raise UploadValidationError("File exceeds the configured size limit")
    sniffed = _sniff_media_type(content)
    if sniffed is None or sniffed not in ALLOWED_MEDIA_TYPES:
        raise UploadValidationError("Only PDF, PNG, and JPEG files are supported")
    if declared_media_type not in {None, "", "application/octet-stream", sniffed}:
        raise UploadValidationError("Declared MIME type does not match file content")
    extension = Path(filename).suffix.casefold()
    valid_extensions = {EXTENSIONS[sniffed]}
    if sniffed == "image/jpeg":
        valid_extensions.add(".jpeg")
    if extension not in valid_extensions:
        raise UploadValidationError("Filename extension does not match file content")

    if sniffed == "application/pdf":
        return sniffed, _validate_pdf(content, settings.document_max_pages), None, None
    width, height = (
        _png_dimensions(content) if sniffed == "image/png" else _jpeg_dimensions(content)
    )
    if width <= 0 or height <= 0 or width * height > settings.document_max_pixels:
        raise UploadValidationError("Image dimensions exceed the configured pixel limit")
    return sniffed, None, width, height


class LocalDocumentTextExtractor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def extract(self, path: Path, media_type: str) -> ExtractionOutput:
        if media_type == "application/pdf":
            return self._extract_pdf(path)
        return self._extract_image(path)

    def _extract_pdf(self, path: Path) -> ExtractionOutput:
        try:
            pypdf: Any = importlib.import_module("pypdf")
        except ModuleNotFoundError as error:
            raise ExtractionUnavailableError(
                "Local PDF text parser is not installed; use manual entry"
            ) from error
        try:
            reader = pypdf.PdfReader(str(path))
            blocks = []
            for index, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    blocks.append(
                        TextBlock(
                            id=f"page-{index}",
                            page=index,
                            text=text,
                            confidence=Decimal("0.95"),
                        )
                    )
        except Exception as error:
            # Third-party parser failures must not turn a document into a 500 response.
            raise ExtractionUnavailableError(
                "PDF text parsing failed; use manual entry"
            ) from error
        if not blocks:
            raise ExtractionUnavailableError(
                "PDF has no embedded text; scanned PDF rendering is unavailable"
            )
        return ExtractionOutput(
            engine="pypdf",
            version=str(getattr(pypdf, "__version__", "unknown")),
            blocks=blocks,
            warnings=["PDF_TEXT_HAS_PAGE_LOCATION_WITHOUT_BOUNDING_BOX"],
        )

    def _extract_image(self, path: Path) -> ExtractionOutput:
        if not self._settings.ocr_enabled:
            raise ExtractionUnavailableError("Local OCR is disabled; use manual entry")
        command = shutil.which(self._settings.tesseract_command)
        if command is None:
            raise ExtractionUnavailableError("Tesseract executable was not found; use manual entry")
        try:
            completed = subprocess.run(
                [
                    command,
                    str(path),
                    "stdout",
                    "-l",
                    self._settings.tesseract_languages,
                    "--psm",
                    "6",
                    "tsv",
                ],
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._settings.document_processing_timeout_seconds,
            )
        except OSError as error:
            raise ExtractionUnavailableError(
                "Tesseract could not be started; use manual entry"
            ) from error
        if completed.returncode != 0:
            raise ExtractionUnavailableError("Tesseract failed; use manual entry")
        blocks: list[TextBlock] = []
        for index, row in enumerate(csv.DictReader(io.StringIO(completed.stdout), delimiter="\t")):
            text = (row.get("text") or "").strip()
            confidence_text = row.get("conf") or "-1"
            try:
                confidence_number = Decimal(confidence_text)
            except InvalidOperation:
                continue
            if not text or confidence_number < 0:
                continue
            blocks.append(
                TextBlock(
                    id=f"ocr-{index}",
                    page=1,
                    text=text,
                    confidence=min(Decimal("1"), confidence_number / 100),
                    bounding_box=BoundingBox(
                        x=int(row.get("left") or 0),
                        y=int(row.get("top") or 0),
                        width=int(row.get("width") or 0),
                        height=int(row.get("height") or 0),
                    ),
                )
            )
        if not blocks:
            raise ExtractionUnavailableError("Tesseract returned no text; use manual entry")
        return ExtractionOutput(
            engine="tesseract",
            version="cli",
            blocks=blocks,
            warnings=["IMAGE_PREPROCESSING_LIMITED_TO_TESSERACT_INTERNAL_PROCESSING"],
        )


def _normalize_money(raw: str) -> str:
    compact = raw.replace(",", "").replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(억원|천만원|백만원|만원|원)?", compact)
    if match is None:
        raise ValueError("money value could not be parsed")
    value = Decimal(match.group(1))
    multiplier = {
        "억원": Decimal("100000000"),
        "천만원": Decimal("10000000"),
        "백만원": Decimal("1000000"),
        "만원": Decimal("10000"),
        "원": Decimal("1"),
        None: Decimal("1"),
    }[match.group(2)]
    return str((value * multiplier).quantize(Decimal("1")))


def _normalize_area(raw: str) -> tuple[str, str]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(㎡|m2|m²|평)", raw, re.IGNORECASE)
    if match is None:
        raise ValueError("area value could not be parsed")
    value = Decimal(match.group(1))
    unit = match.group(2)
    sqm = value * Decimal("3.3058") if unit == "평" else value
    return str(sqm.quantize(Decimal("0.01"))), unit


FIELD_PATTERNS: dict[DocumentFieldName, re.Pattern[str]] = {
    DocumentFieldName.ADDRESS: re.compile(r"(?:소재지|주소)\s*[:\uFF1A]?\s*([^\n]{4,120})"),
    DocumentFieldName.DEPOSIT: re.compile(
        r"(?:임차)?보증금\s*[:\uFF1A]?\s*([\d,.]+\s*(?:억원|천만원|백만원|만원|원)?)"
    ),
    DocumentFieldName.MONTHLY_RENT: re.compile(
        r"(?:월세|월차임|차임)\s*[:\uFF1A]?\s*([\d,.]+\s*(?:백만원|만원|원)?)"
    ),
    DocumentFieldName.MAINTENANCE_FEE: re.compile(
        r"관리비\s*[:\uFF1A]?\s*([\d,.]+\s*(?:백만원|만원|원)?)"
    ),
    DocumentFieldName.MAINTENANCE_INCLUDED_ITEMS: re.compile(
        r"(?:관리비\s*)?(?:포함항목|포함)\s*[:\uFF1A]?\s*([^\n]{1,150})"
    ),
    DocumentFieldName.AREA_SQM: re.compile(
        r"(?:전용면적|면적)\s*[:\uFF1A]?\s*(\d+(?:\.\d+)?\s*(?:㎡|m2|m²|평))",
        re.IGNORECASE,
    ),
    DocumentFieldName.CONTRACT_PERIOD: re.compile(
        r"(?:계약기간|임대차기간)\s*[:\uFF1A]?\s*([^\n]{4,100})"
    ),
    DocumentFieldName.SPECIAL_TERMS: re.compile(
        r"(?:특약사항|특약)\s*[:\uFF1A]?\s*([^\n]{1,500})"
    ),
    DocumentFieldName.BROKERAGE_FEE: re.compile(
        r"(?:중개보수|중개수수료)\s*[:\uFF1A]?\s*([^\n]{1,150})"
    ),
}


def _field_confidence(raw: str, blocks: list[TextBlock]) -> tuple[Decimal | None, list[str]]:
    matching = [block for block in blocks if raw in block.text or block.text in raw]
    confidences = [block.confidence for block in matching if block.confidence is not None]
    confidence = (
        sum(confidences, Decimal("0")) / len(confidences) if confidences else Decimal("0.70")
    )
    return confidence, [block.id for block in matching]


def extract_structured_fields(
    masked_text: str, blocks: list[TextBlock]
) -> tuple[list[ExtractedFieldValue], list[str]]:
    fields: list[ExtractedFieldValue] = []
    contradictions: list[str] = []
    for name, pattern in FIELD_PATTERNS.items():
        matches = [match.strip() for match in pattern.findall(masked_text)]
        if not matches:
            continue
        normalized_values: list[tuple[str, str | None, str]] = []
        for raw in matches:
            try:
                if name in {
                    DocumentFieldName.DEPOSIT,
                    DocumentFieldName.MONTHLY_RENT,
                    DocumentFieldName.MAINTENANCE_FEE,
                }:
                    normalized, unit = _normalize_money(raw), "KRW"
                elif name is DocumentFieldName.AREA_SQM:
                    normalized, original_unit = _normalize_area(raw)
                    unit = "sqm"
                    fields.append(
                        ExtractedFieldValue(
                            id=uuid4(),
                            name=DocumentFieldName.AREA_ORIGINAL,
                            raw_text=raw,
                            normalized_value=raw,
                            unit=original_unit,
                            confidence=_field_confidence(raw, blocks)[0],
                            source_block_ids=_field_confidence(raw, blocks)[1],
                            status=FieldStatus.PROPOSED,
                        )
                    )
                else:
                    normalized, unit = raw, None
                normalized_values.append((normalized, unit, raw))
            except (ValueError, InvalidOperation):
                contradictions.append(f"{name.value}:INVALID_VALUE_OR_UNIT")
        if not normalized_values:
            continue
        distinct = {item[0] for item in normalized_values}
        if len(distinct) > 1:
            contradictions.append(f"{name.value}:MULTIPLE_DIFFERENT_VALUES")
        normalized, unit, raw = normalized_values[0]
        confidence, source_ids = _field_confidence(raw, blocks)
        needs_review = confidence is None or confidence < Decimal("0.75") or len(distinct) > 1
        fields.append(
            ExtractedFieldValue(
                id=uuid4(),
                name=name,
                raw_text=raw,
                normalized_value=normalized,
                unit=unit,
                confidence=confidence,
                source_block_ids=source_ids,
                status=FieldStatus.NEEDS_REVIEW if needs_review else FieldStatus.PROPOSED,
            )
        )
    return fields, contradictions


def evaluate_extraction_accuracy(
    analyses: list[DocumentAnalysis],
    expected_documents: list[dict[DocumentFieldName, str]],
) -> ExtractionEvaluation:
    if len(analyses) != len(expected_documents) or not analyses:
        raise ValueError("analyses and expected documents must have the same non-zero length")
    counters: dict[DocumentFieldName, list[int]] = {}
    for analysis, expected in zip(analyses, expected_documents, strict=True):
        predicted = {field.name: field.normalized_value for field in analysis.fields}
        for field_name, expected_value in expected.items():
            counter = counters.setdefault(field_name, [0, 0])
            counter[0] += 1
            counter[1] += int(predicted.get(field_name) == expected_value)
    field_metrics = [
        FieldAccuracy(
            field_name=field_name,
            expected_count=expected_count,
            exact_match_count=match_count,
            exact_match_accuracy=Decimal(match_count) / expected_count,
        )
        for field_name, (expected_count, match_count) in sorted(
            counters.items(), key=lambda item: item[0].value
        )
    ]
    macro = sum(
        (metric.exact_match_accuracy for metric in field_metrics), Decimal("0")
    ) / len(field_metrics)
    return ExtractionEvaluation(
        document_count=len(analyses),
        fields=field_metrics,
        macro_accuracy=macro,
    )


def _normalize_review_value(field_name: DocumentFieldName, value: str) -> str:
    if field_name in {
        DocumentFieldName.DEPOSIT,
        DocumentFieldName.MONTHLY_RENT,
        DocumentFieldName.MAINTENANCE_FEE,
    }:
        return _normalize_money(value)
    if field_name is DocumentFieldName.AREA_SQM:
        return _normalize_area(value)[0]
    return value.strip()


@dataclass(slots=True)
class StoredDocumentAnalysis:
    path: Path
    analysis: DocumentAnalysis


class DocumentService:
    def __init__(self, settings: Settings, extractor: DocumentTextExtractor | None = None) -> None:
        self._settings = settings
        self._upload_dir = settings.resolved_upload_dir
        self._extractor = extractor or LocalDocumentTextExtractor(settings)
        self._records: dict[UUID, StoredDocumentAnalysis] = {}

    def upload(
        self,
        data_repository: DataRepository,
        session_id: UUID,
        access_token: str,
        *,
        filename: str,
        declared_media_type: str | None,
        content: bytes,
    ) -> UploadedDocument:
        data_repository.authorize_session(session_id, access_token)
        data_repository.register_cleanup_callback(self.delete_session_records)
        media_type, page_count, width, height = validate_upload(
            filename=filename,
            declared_media_type=declared_media_type,
            content=content,
            settings=self._settings,
        )
        document_id = uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=self._settings.document_retention_hours)
        session_dir = (self._upload_dir / str(session_id)).resolve()
        if not session_dir.is_relative_to(self._upload_dir):
            raise UploadValidationError("Resolved upload path escaped upload root")
        session_dir.mkdir(parents=True, exist_ok=True)
        storage_path = (session_dir / f"{document_id}{EXTENSIONS[media_type]}").resolve()
        if not storage_path.is_relative_to(session_dir):
            raise UploadValidationError("Resolved document path escaped session directory")
        storage_path.write_bytes(content)
        uploaded = UploadedDocument(
            id=document_id,
            session_id=session_id,
            original_filename=filename,
            media_type=media_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            page_count=page_count,
            image_width=width,
            image_height=height,
            status=DocumentStatus.STORED,
            extractor=None,
            extraction_version=None,
            warnings=[],
            expires_at=expires_at,
            created_at=now,
        )
        analysis = DocumentAnalysis(
            document=uploaded,
            masked_text="",
            blocks=[],
            fields=[],
            missing_required_fields=sorted(REQUIRED_FIELDS),
            contradictions=[],
            injection_detected=False,
        )
        self._records[document_id] = StoredDocumentAnalysis(path=storage_path, analysis=analysis)
        data_repository.register_document(
            session_id,
            access_token,
            metadata=SourceDocument(
                id=document_id,
                session_id=session_id,
                original_filename=filename,
                sha256=uploaded.sha256,
                media_type=media_type,
                expires_at=expires_at,
                masked=False,
                created_at=now,
            ),
            storage_path=storage_path,
        )
        return uploaded

    def delete_session_records(self, session_id: UUID) -> int:
        document_ids = [
            document_id
            for document_id, record in self._records.items()
            if record.analysis.document.session_id == session_id
        ]
        for document_id in document_ids:
            del self._records[document_id]
        return len(document_ids)

    def _authorized_record(
        self,
        data_repository: DataRepository,
        session_id: UUID,
        access_token: str,
        document_id: UUID,
    ) -> StoredDocumentAnalysis:
        data_repository.authorize_session(session_id, access_token)
        record = self._records.get(document_id)
        if record is None or record.analysis.document.session_id != session_id:
            raise DocumentNotFoundError(document_id)
        return record

    def extract(
        self,
        data_repository: DataRepository,
        session_id: UUID,
        access_token: str,
        document_id: UUID,
    ) -> DocumentAnalysis:
        record = self._authorized_record(
            data_repository, session_id, access_token, document_id
        )
        try:
            output = self._extractor.extract(record.path, record.analysis.document.media_type)
        except (ExtractionUnavailableError, subprocess.TimeoutExpired) as error:
            document = record.analysis.document.model_copy(
                update={
                    "status": DocumentStatus.MANUAL_REQUIRED,
                    "warnings": ["EXTRACTION_UNAVAILABLE", str(error)],
                }
            )
            record.analysis = record.analysis.model_copy(update={"document": document})
            return record.analysis
        raw_text = "\n".join(block.text for block in output.blocks)
        injection_detected = contains_prompt_injection(raw_text)
        masked_blocks = [
            block.model_copy(update={"text": mask_sensitive_text(block.text)})
            for block in output.blocks
        ]
        masked_text = mask_sensitive_text(raw_text)
        fields, contradictions = extract_structured_fields(masked_text, masked_blocks)
        found = {field.name.value for field in fields}
        warnings = list(output.warnings)
        if injection_detected:
            warnings.append("DOCUMENT_INSTRUCTION_TREATED_AS_UNTRUSTED_DATA")
        if contradictions:
            warnings.append("CONTRADICTORY_OR_INVALID_FIELDS_REQUIRE_REVIEW")
        document = record.analysis.document.model_copy(
            update={
                "status": DocumentStatus.EXTRACTED,
                "extractor": output.engine,
                "extraction_version": EXTRACTION_VERSION,
                "warnings": warnings,
            }
        )
        record.analysis = DocumentAnalysis(
            document=document,
            masked_text=masked_text,
            blocks=masked_blocks,
            fields=fields,
            missing_required_fields=sorted(REQUIRED_FIELDS - found),
            contradictions=contradictions,
            injection_detected=injection_detected,
        )
        return record.analysis

    def get_analysis(
        self,
        data_repository: DataRepository,
        session_id: UUID,
        access_token: str,
        document_id: UUID,
    ) -> DocumentAnalysis:
        return self._authorized_record(
            data_repository, session_id, access_token, document_id
        ).analysis

    def review_field(
        self,
        data_repository: DataRepository,
        session_id: UUID,
        access_token: str,
        document_id: UUID,
        field_name: DocumentFieldName,
        payload: FieldReviewInput,
    ) -> DocumentAnalysis:
        record = self._authorized_record(
            data_repository, session_id, access_token, document_id
        )
        reviewed_value = _normalize_review_value(field_name, payload.value)
        fields = list(record.analysis.fields)
        existing_index = next(
            (index for index, field in enumerate(fields) if field.name is field_name), None
        )
        now = datetime.now(UTC) if payload.confirmed else None
        if existing_index is None:
            fields.append(
                ExtractedFieldValue(
                    id=uuid4(),
                    name=field_name,
                    raw_text="",
                    normalized_value=reviewed_value,
                    confidence=None,
                    source_block_ids=[],
                    status=FieldStatus.CONFIRMED if payload.confirmed else FieldStatus.NEEDS_REVIEW,
                    confirmed_value=reviewed_value if payload.confirmed else None,
                    confirmed_at=now,
                )
            )
        else:
            existing = fields[existing_index]
            corrected = reviewed_value != existing.normalized_value
            fields[existing_index] = existing.model_copy(
                update={
                    "confirmed_value": reviewed_value if payload.confirmed else None,
                    "confirmed_at": now,
                    "status": (
                        FieldStatus.CORRECTED
                        if payload.confirmed and corrected
                        else FieldStatus.CONFIRMED
                        if payload.confirmed
                        else FieldStatus.NEEDS_REVIEW
                    ),
                }
            )
        found = {field.name.value for field in fields}
        record.analysis = record.analysis.model_copy(
            update={
                "fields": fields,
                "missing_required_fields": sorted(REQUIRED_FIELDS - found),
            }
        )
        return record.analysis

    def confirmed_fields(
        self,
        data_repository: DataRepository,
        session_id: UUID,
        access_token: str,
        document_id: UUID,
    ) -> ConfirmedDocumentFields:
        analysis = self.get_analysis(data_repository, session_id, access_token, document_id)
        confirmed = {
            field.name.value: field.confirmed_value
            for field in analysis.fields
            if field.confirmed_value is not None
            and field.status in {FieldStatus.CONFIRMED, FieldStatus.CORRECTED}
        }
        missing = sorted(REQUIRED_FIELDS - confirmed.keys())
        return ConfirmedDocumentFields(
            document_id=document_id,
            values=confirmed,
            missing_confirmed_fields=missing,
            ready_for_calculation=not missing,
        )
