# 집결정 AI

청년이 월세·보증부 월세 후보의 실질 주거비, 정책 적격성, 통근 조건과 선호도를 함께 비교하도록 돕는 주거 의사결정 에이전트입니다.

## 저장소 구성

```text
apps/
  web/                    Next.js 사용자 인터페이스
  api/                    FastAPI 백엔드
outputs/                  기획안과 개발 로드맵
```

## 빠른 시작

필수 환경은 Node.js 22 이상, npm 10 이상과 Python 3.12입니다. Ollama와 로컬 `qwen3:4b`는 설명 문장 고도화에 선택적으로 사용하며, 없어도 수동 입력과 핵심 계산·비교가 작동합니다. Docker는 향후 PostgreSQL 연동 작업에만 필요합니다.

```powershell
Copy-Item .env.example .env
ollama pull qwen3:4b
npm install
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.lock
npm run db:up
npm run dev
```

현재 핵심 흐름은 메모리 저장소를 사용하므로 처음 실행할 때 `npm run db:up`은 생략할 수 있습니다.

웹은 `http://localhost:3000`, API 문서는 `http://localhost:8000/docs`, API 상태는 `http://localhost:8000/health`, 로컬 LLM 상태는 `http://localhost:8000/system/llm`, 개발용 비식별 지표는 `http://localhost:8000/system/metrics`에서 확인합니다.

## 주요 명령

```text
npm run dev        Web·API 동시 실행
npm run lint       코드 오류 검사
npm run test       테스트 실행
npm run eval:release  고정 골든 세트 출시 평가
npm run ops:check     현재 환경의 배포 차단·경고 검사
npm run demo:check    합성 데모 3세트 오프라인 재현
npm run typecheck  타입 검사
npm run build      Web 프로덕션 빌드
npm run db:up      로컬 PostgreSQL 시작
npm run db:down    로컬 PostgreSQL 종료
```

## 간단한 Git 협업

```powershell
git switch main
git pull
git switch -c feat/작업명

# 작업 후
git add <변경한 파일>
git commit -m "feat: 작업 내용"
git push -u origin feat/작업명
```

GitHub에서 Pull Request를 만들고 상대 팀원이 확인한 뒤 merge합니다.

## 현재 단계

Phase 12 로컬 배포·시연·운영 준비까지 구현됐습니다. 합성 데모 3세트와 자동 출시 평가는 모두 통과하지만 실제 390px 모바일 확인, 정책 독립 검토, 다른 PC 재현이 남아 로컬 데모는 조건부 준비 상태입니다. PostgreSQL 어댑터와 운영 보안 인프라가 없어 공개 프로덕션 배포는 차단합니다.

- [기획안](outputs/청년_주거_금융_도우미_기획안.md)
- [단계별 개발 로드맵](outputs/집결정_AI_단계별_개발_로드맵.md)
- [Phase 0 범위·요구사항](outputs/Phase_0_범위_요구사항_확정.md)
- [Phase 1 UX·아키텍처·개발 기반](outputs/Phase_1_UX_아키텍처_개발_기반.md)
- [Phase 2 데이터 모델·개인정보 기반](outputs/Phase_2_데이터_모델_개인정보_기반.md)
- [Phase 3 주거비 계산 엔진](outputs/Phase_3_주거비_계산_엔진.md)
- [Phase 4 정책 데이터·규칙 엔진](outputs/Phase_4_정책_데이터_규칙_엔진.md)
- [Phase 5 후보 비교·다기준 최적화](outputs/Phase_5_후보_비교_다기준_최적화.md)
- [Phase 6 문서 업로드·OCR·구조화 추출](outputs/Phase_6_문서_업로드_OCR_구조화_추출.md)
- [Phase 7 정책 RAG·근거 관리](outputs/Phase_7_정책_RAG_근거_관리.md)
- [Phase 8 에이전트 오케스트레이션](outputs/Phase_8_에이전트_오케스트레이션.md)
- [Phase 9 Web UI·의사결정 카드](outputs/Phase_9_Web_UI_의사결정_카드.md)
- [Phase 10 안전성·보안·관측성](outputs/Phase_10_안전성_보안_관측성.md)
- [Phase 11 통합 테스트·평가·레드팀](outputs/Phase_11_통합_테스트_평가_레드팀.md)
- [Phase 12 배포·시연·운영 준비](outputs/Phase_12_배포_시연_운영_준비.md)
