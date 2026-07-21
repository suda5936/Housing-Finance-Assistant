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

필요 환경은 Node.js 22 이상, npm 10 이상, Python 3.12, Docker Desktop, Ollama입니다. LLM은 유료 API 대신 로컬 `qwen3:4b`를 사용합니다.

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

웹은 `http://localhost:3000`, API 문서는 `http://localhost:8000/docs`, API 상태는 `http://localhost:8000/health`, 로컬 LLM 상태는 `http://localhost:8000/system/llm`에서 확인합니다.

## 주요 명령

```text
npm run dev        Web·API 동시 실행
npm run lint       코드 오류 검사
npm run test       테스트 실행
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

Phase 1 UX·아키텍처·개발 기반이 완료된 상태입니다. 비즈니스 기능은 Phase 2 데이터 모델부터 개발 로드맵의 순서대로 추가합니다.

- [기획안](outputs/청년_주거_금융_도우미_기획안.md)
- [단계별 개발 로드맵](outputs/집결정_AI_단계별_개발_로드맵.md)
- [Phase 0 범위·요구사항](outputs/Phase_0_범위_요구사항_확정.md)
- [Phase 1 UX·아키텍처·개발 기반](outputs/Phase_1_UX_아키텍처_개발_기반.md)
