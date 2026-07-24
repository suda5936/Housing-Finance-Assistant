# Phase 6 문서 업로드·OCR·구조화 추출

작성 기준일: 2026-07-24  
구현 상태: 완료

## 1. 목표와 처리 원칙

매물 이미지와 계약서 초안에서 주거비 계산과 후보 비교에 필요한 필드를 추출하고, 원문 위치와 함께 사용자가 수정·확정할 수 있게 한다.

- 유료 OCR API를 사용하지 않는다.
- PDF 내장 텍스트는 로컬 `pypdf`로 추출한다.
- PNG·JPEG 이미지는 로컬 Tesseract CLI 어댑터를 사용한다.
- OCR이나 PDF 파서가 없거나 실패하면 `manual_required`로 전환한다.
- Qwen3-4B는 OCR, 금액 계산 또는 필드 확정에 사용하지 않는다.
- 문서에 적힌 AI 지시문은 신뢰하지 않는 데이터로만 저장한다.
- 추출값은 사용자가 확정하기 전까지 계산 입력으로 내보내지 않는다.

`pypdf`는 프로젝트 Python 의존성에 포함했다. 현재 개발 PC에는 Tesseract 실행 파일이 없으므로 이미지 OCR 기본값은 `OCR_ENABLED=false`이며, 서비스는 자동으로 수동 입력 경로를 제공한다.

## 2. 업로드 제한

| 항목 | 기본 제한 |
| --- | ---: |
| 지원 형식 | PDF, PNG, JPEG |
| 파일 크기 | 10 MiB |
| PDF 페이지 | 10페이지 |
| 이미지 전체 픽셀 | 25,000,000 픽셀 |
| 처리 제한시간 | 30초 |
| 원본 보관 | 기본 24시간 |

확장자, 요청의 MIME, 파일 매직바이트를 함께 검사한다. 파일명에 디렉터리 경로가 포함되면 거부하고, 서버가 생성한 UUID 파일명으로 세션별 업로드 루트 안에 저장한다.

PDF에서 JavaScript, Launch action, 첨부파일, OpenAction 토큰이 발견되면 처리하지 않는다. 이는 완전한 백신 엔진을 대신하지 않지만 MVP에서 활성 PDF 요소를 허용하지 않는 방어선이다.

## 3. 로컬 추출기와 폴백

### 텍스트 PDF

`pypdf`로 페이지별 내장 텍스트를 추출한다. 페이지 번호는 제공하지만 이 방식은 글자별 좌표를 제공하지 않으므로 `bounding_box=null`과 `PDF_TEXT_HAS_PAGE_LOCATION_WITHOUT_BOUNDING_BOX` 경고를 남긴다.

스캔된 PDF에 내장 텍스트가 없으면 별도 PDF 렌더러를 임의 설치하지 않고 수동 입력으로 전환한다.

### PNG·JPEG

`OCR_ENABLED=true`이고 설정한 Tesseract 명령이 PATH에 있을 때 TSV 출력을 사용한다. 각 단어 블록에 페이지, 좌표, 너비·높이와 0~1 신뢰도를 저장한다.

현재 어댑터는 Tesseract 내부 처리만 사용하므로 `IMAGE_PREPROCESSING_LIMITED_TO_TESSERACT_INTERNAL_PROCESSING` 경고를 표시한다. 회전·노이즈 제거·스캔 PDF 렌더링 고도화는 실제 평가셋에서 필요성이 확인된 뒤 추가한다.

### 수동 폴백

추출기가 꺼져 있거나 설치되지 않았거나 텍스트를 찾지 못하면 HTTP 오류로 흐름을 끝내지 않는다. 문서 상태를 `manual_required`로 바꾸고 기존 후보 수동 등록 API 또는 필드 수정 API로 계속 진행한다.

## 4. 추출 필드와 정규화

| 필드 | 정규화 |
| --- | --- |
| 주소 | 원문 문자열 |
| 보증금 | KRW 정수 문자열 |
| 월세 | KRW 정수 문자열 |
| 관리비 | KRW 정수 문자열 |
| 관리비 포함항목 | 원문 문자열 |
| 면적 | ㎡, 소수 둘째 자리 |
| 면적 원단위 | ㎡·m²·평 원문 보존 |
| 계약기간 | 원문 기간 문자열 |
| 특약사항 | 원문 문자열 |
| 중개보수 | 원문 문자열 |

`억원`, `천만원`, `백만원`, `만원`, `원`을 원 단위로 바꾸며 `평`은 `3.3058㎡`로 변환한다. 파싱할 수 없는 금액이나 면적 단위는 모순 목록에 남기고, 사용자의 수정 입력도 같은 검증기를 통과해야 한다.

같은 필드가 서로 다른 값으로 여러 번 나타나면 첫 값을 자동 확정하지 않고 `MULTIPLE_DIFFERENT_VALUES`와 `needs_review` 상태를 반환한다. 신뢰도 0.75 미만도 확인 대상으로 분류한다.

## 5. 원문 근거와 개인정보

이미지 OCR 블록은 `page`, `bounding_box`, `confidence`를 가진다. 각 추출 필드는 근거가 된 `source_block_ids`를 반환한다. PDF 내장 텍스트는 페이지 단위 근거를 제공한다.

분석용 텍스트와 블록을 저장하기 전에 다음을 `[REDACTED]`로 마스킹한다.

- 주민등록번호 형태
- 휴대전화번호 형태
- 이메일 주소
- 하이픈으로 구분된 계좌번호 형태

원본 파일은 Phase 2 정책에 따라 제한적으로 보관되고 세션 삭제나 24시간 만료 시 함께 삭제된다.

## 6. 프롬프트 인젝션 처리

`ignore previous instructions`, `system prompt`, `이전 지시 무시`, `AI에게 명령` 같은 문자열을 탐지한다. 탐지 여부와 경고는 남기지만 문서 내용을 실행하거나 LLM 시스템 지시로 전달하지 않는다. 구조화 추출은 고정된 정규식과 후처리 코드만 사용한다.

## 7. 사용자 검토와 계산 차단

각 필드는 다음 상태 중 하나다.

- `proposed`: 자동 추출 후보
- `needs_review`: 저신뢰 또는 모순
- `confirmed`: 사용자가 원값을 확인
- `corrected`: 사용자가 다른 값으로 수정·확정

확정 전 값은 `confirmed-fields` 응답에 포함되지 않는다. 주소, 보증금, 월세, 관리비, 면적, 계약기간이 모두 확정되어야 `ready_for_calculation=true`가 된다. 자동 추출값과 수정값을 별도 필드로 유지하므로 변경 전후를 확인할 수 있다.

## 8. API

```http
POST /sessions/{session_id}/documents
POST /sessions/{session_id}/documents/{document_id}/extract
GET  /sessions/{session_id}/documents/{document_id}
PUT  /sessions/{session_id}/documents/{document_id}/fields/{field_name}
GET  /sessions/{session_id}/documents/{document_id}/confirmed-fields
```

모든 경로는 `X-Session-Token`을 요구한다. 잘못된 세션 토큰으로 다른 세션의 문서나 분석 결과를 읽을 수 없다.

## 9. 정확도 평가

`evaluate_extraction_accuracy()`는 비식별 합성 문서의 정답과 정규화된 추출값을 필드별로 비교한다.

- 필드별 정답 수
- 필드별 정확 일치 수
- 필드별 exact-match 정확도
- 필드 정확도의 macro 평균

현재 합성 기준 문서에서는 주소·보증금·월세·관리비·면적·계약기간 6개 핵심 필드가 모두 정확히 일치했다. 이는 실제 촬영 이미지 성능을 의미하지 않는다. 실제 OCR 정확도는 Tesseract 설치 후 다양한 해상도·회전·표 형식의 별도 비식별 평가셋으로 다시 측정해야 한다.

## 10. 구현 파일

- 문서 도메인·검증·추출기: `apps/api/homefit_api/documents.py`
- 문서 API: `apps/api/homefit_api/document_api.py`
- 합성 평가·보안 테스트: `apps/api/tests/test_documents.py`
- DB 마이그레이션: `apps/api/migrations/004_phase6_documents.sql`
- 설정: `apps/api/homefit_api/settings.py`, `.env.example`

## 11. 완료 점검

- [x] 파일 형식·크기·페이지·픽셀 제한을 구현했다.
- [x] 확장자·MIME·매직바이트를 교차검증한다.
- [x] 활성 PDF 요소와 경로 조작을 차단한다.
- [x] PDF 텍스트와 이미지 OCR 어댑터를 분리했다.
- [x] OCR 미설치·실패 시 수동 입력으로 전환한다.
- [x] OCR 블록 좌표·신뢰도와 필드 원문 근거를 연결한다.
- [x] 금액·면적 단위를 정규화하고 잘못된 값을 검증한다.
- [x] 저신뢰·중복값을 확인 대상으로 표시한다.
- [x] 개인정보 패턴을 분석 텍스트에서 마스킹한다.
- [x] 문서 지시문을 실행하지 않는 인젝션 테스트가 있다.
- [x] 사용자 수정 전후 값과 확정 상태를 분리한다.
- [x] 미확정 필드를 계산용 응답에서 제외한다.
- [x] 필드별 정확도 측정기를 구현했다.

## 12. 최종 검증 결과

2026-07-24 기준으로 다음 검증을 통과했다.

- 백엔드 전체 테스트: 74개 통과
- 프런트엔드 테스트: 2개 통과
- Python 정적 검사(Ruff): 통과
- Python 타입 검사(mypy): 통과
- 전체 백엔드 코드 커버리지: 88%

문서 추출 테스트는 합성 텍스트와 가짜 OCR 어댑터를 사용한다. 따라서 이 결과는 실제 촬영 이미지에서의 Tesseract 정확도를 보장하지 않는다. 실제 OCR 품질 평가는 Tesseract와 한국어 언어 데이터를 설치한 뒤 별도 실문서 평가셋으로 진행해야 한다.

현재 제한된 개발 실행 환경에서는 외부 패키지 다운로드가 차단되어 `pypdf`를 가상환경에 즉시 설치하지 못했다. 의존성 선언과 고정 버전은 반영되어 있으므로 네트워크가 가능한 일반 터미널에서 `requirements-dev.lock`을 설치하면 된다. 설치 전에도 서버는 PDF 파싱 실패를 500 오류로 노출하지 않고 수동 입력 상태로 전환한다.
