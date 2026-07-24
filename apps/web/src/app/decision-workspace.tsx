"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, requestJson } from "@/lib/api";
import {
  CandidateForm,
  ProfileForm,
  WEIGHT_PRESETS,
  WeightPreset,
  contributionLabel,
  downloadFileName,
  money,
  percent,
  validateCandidates,
  validateProfile,
} from "@/lib/decision-model";

type Step = "intro" | "profile" | "candidates" | "document" | "analysis" | "result";
type Session = { session: { id: string; expires_at: string }; access_token: string };
type CostResponse = {
  status: "calculated" | "missing_information";
  calculation_version: string;
  input_sha256: string;
  missing_fields: string[];
  scenarios: Array<{
    scenario: "optimistic" | "base" | "conservative";
    breakdown: {
      monthly_effective_cost: { amount: string; currency: "KRW" };
      contract_total_cost: { amount: string; currency: "KRW" };
      housing_cost_burden_percent: string;
      monthly_rent: { amount: string };
      monthly_maintenance: { amount: string };
      monthly_borrowing_cost: { amount: string };
      monthly_commute_cost: { amount: string };
    };
    assumptions: Record<string, unknown>;
    reason_codes: string[];
  }>;
};
type RankingCandidate = {
  candidate_id: string;
  label: string;
  district: string;
  monthly_effective_cost: string;
  cost_scenario: "base";
  cost_calculation_version: string;
  cost_input_sha256: string;
  commute_minutes: string;
  commute_reference_at: string;
  deposit: string;
  area_sqm: string;
  risk_score: string;
  risk_basis: string;
  policy_statuses: string[];
  policy_versions: string[];
};
type RankingResponse = {
  status: "ranked" | "partial" | "not_comparable";
  ranking_version: string;
  input_sha256: string;
  weights: Record<string, string>;
  results: Array<{
    candidate_id: string;
    label: string;
    rank: number | null;
    total_score: string | null;
    disposition: string;
    contributions: Array<{
      criterion: string;
      raw_value: string;
      weight_percent: string;
      contribution: string;
    }>;
    hard_constraint_failures: string[];
    missing_fields: string[];
    reason_codes: string[];
  }>;
  tradeoffs: Array<{
    candidate_id: string;
    compared_with_id: string;
    advantages: string[];
    disadvantages: string[];
  }>;
  sensitivity: { winner_changes: boolean } | null;
  warnings: string[];
};
type DocumentAnalysis = {
  document: { id: string; status: "stored" | "extracted" | "manual_required" | "failed"; warnings: string[] };
  fields: Array<{
    name: string;
    normalized_value: string;
    confidence: string | null;
    status: string;
  }>;
  missing_required_fields: string[];
  injection_detected: boolean;
};
type Citation = {
  id: string;
  title: string;
  institution: string;
  url: string;
  locator: string;
  quote: string;
  checked_on: string;
  review_status: string;
  retrieval_status: string;
};
type AgentRun = {
  id: string;
  state: "decision_card" | "official_check" | "clarification" | "failed" | string;
  decision_card: null | {
    status: string;
    winner_candidate_ids: string[];
    summary_sentences: string[];
    warnings: string[];
    disclaimer: string;
    checklist: Array<{
      code: string;
      action: string;
      version: string;
      verification_actor: string;
      disclaimer: string;
      citations: Citation[];
    }>;
  };
  official_check_reasons: string[];
  verification_gates: Array<{ code: string; passed: boolean; reason: string }>;
};
type ResultBundle = {
  costs: Record<string, CostResponse>;
  ranking: RankingResponse;
  rankingCandidates: RankingCandidate[];
  agent: AgentRun;
  analyzedAt: string;
};

const STEPS: Array<{ id: Step; label: string }> = [
  { id: "intro", label: "동의" },
  { id: "profile", label: "내 조건" },
  { id: "candidates", label: "후보" },
  { id: "document", label: "문서" },
  { id: "analysis", label: "분석" },
  { id: "result", label: "결과" },
];

const emptyProfile: ProfileForm = {
  ageYears: "",
  monthlyIncome: "",
  liquidAssets: "",
  availableDeposit: "",
  medianIncomeRatio: "",
  initialCosts: "",
  annualRate: "",
  householdType: "single",
  workplaceDistrict: "",
  isHomeless: true,
  receivedSeoulSupport: false,
  receivingOtherSupport: false,
};

function candidate(localId: string, label = ""): CandidateForm {
  return {
    localId,
    label,
    district: "",
    deposit: "",
    monthlyRent: "",
    maintenance: "",
    areaSqm: "",
    contractMonths: "12",
    commuteMinutes: "",
    commuteCost: "",
    riskScore: "",
    riskBasis: "",
  };
}

function moneyValue(amount: string) {
  return { amount, currency: "KRW" };
}

function candidatePayload(item: CandidateForm) {
  return {
    label: item.label,
    district: item.district,
    deposit: moneyValue(item.deposit),
    monthly_rent: moneyValue(item.monthlyRent),
    monthly_maintenance: moneyValue(item.maintenance),
    area_sqm: item.areaSqm,
    contract_months: Number(item.contractMonths),
    commute_minutes_one_way: Number(item.commuteMinutes),
    monthly_commute_cost: moneyValue(item.commuteCost),
  };
}

function costPayload(profile: ProfileForm, item: CandidateForm) {
  const ownFunds = Math.min(Number(profile.availableDeposit), Number(item.deposit));
  const now = new Date().toISOString();
  return {
    candidate_label: item.label,
    candidate_district: item.district,
    monthly_net_income: moneyValue(profile.monthlyIncome),
    deposit: moneyValue(item.deposit),
    own_funds_for_deposit: moneyValue(String(ownFunds)),
    monthly_rent: moneyValue(item.monthlyRent),
    monthly_maintenance: {
      minimum: moneyValue(item.maintenance),
      base: moneyValue(item.maintenance),
      maximum: moneyValue(item.maintenance),
    },
    annual_borrowing_rate: {
      minimum: profile.annualRate,
      base: profile.annualRate,
      maximum: profile.annualRate,
    },
    annual_own_funds_opportunity_rate: { minimum: "0", base: "0", maximum: "0" },
    contract_months: Number(item.contractMonths),
    initial_costs: moneyValue(profile.initialCosts || "0"),
    commute: {
      source: "manual",
      transport_mode: "public_transit",
      reference_at: now,
      commute_minutes_one_way: Number(item.commuteMinutes),
      monthly_cost: moneyValue(item.commuteCost),
    },
    monthly_living_cost: moneyValue("0"),
    supports: [],
  };
}

function baseScenario(cost: CostResponse) {
  return cost.scenarios.find((item) => item.scenario === "base") ?? cost.scenarios[0];
}

function Field({
  label,
  hint,
  suffix,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
  suffix?: string;
}) {
  const id = props.id ?? props.name;
  return (
    <label className="field" htmlFor={id}>
      <span className="field-label">{label}</span>
      <span className="input-shell">
        <input {...props} id={id} />
        {suffix ? <span className="input-suffix">{suffix}</span> : null}
      </span>
      {hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  );
}

export function DecisionWorkspace() {
  const [step, setStep] = useState<Step>("intro");
  const [consent, setConsent] = useState({ privacy: false, sensitive: false });
  const [profile, setProfile] = useState<ProfileForm>(emptyProfile);
  const [candidates, setCandidates] = useState<CandidateForm[]>([
    candidate("candidate-a", "후보 A"),
    candidate("candidate-b", "후보 B"),
  ]);
  const [session, setSession] = useState<Session | null>(null);
  const [documentFile, setDocumentFile] = useState<File | null>(null);
  const [documentAnalysis, setDocumentAnalysis] = useState<DocumentAnalysis | null>(null);
  const [result, setResult] = useState<ResultBundle | null>(null);
  const [weightPreset, setWeightPreset] = useState<WeightPreset>("balanced");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("입력을 기다리고 있습니다.");
  const [error, setError] = useState<{ message: string; recovery: string } | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const currentStepIndex = STEPS.findIndex((item) => item.id === step);
  const winner = useMemo(() => {
    if (!result) return null;
    const ranking = result.ranking.results.find((item) => item.rank === 1);
    return ranking ? candidates.find((item) => item.serverId === ranking.candidate_id) ?? null : null;
  }, [candidates, result]);

  useEffect(() => {
    headingRef.current?.focus();
  }, [step]);

  function showError(cause: unknown, fallback: string) {
    if (cause instanceof ApiError) {
      setError({ message: cause.message, recovery: cause.recovery });
    } else {
      setError({ message: "예상하지 못한 오류가 발생했습니다.", recovery: fallback });
    }
  }

  function fillDemo() {
    setProfile({
      ageYears: "27",
      monthlyIncome: "3000000",
      liquidAssets: "20000000",
      availableDeposit: "10000000",
      medianIncomeRatio: "100",
      initialCosts: "1200000",
      annualRate: "4",
      householdType: "single",
      workplaceDistrict: "서울 중구",
      isHomeless: true,
      receivedSeoulSupport: false,
      receivingOtherSupport: false,
    });
    setCandidates([
      {
        ...candidate("candidate-a"),
        label: "망원동 햇살집",
        district: "서울 마포구",
        deposit: "10000000",
        monthlyRent: "550000",
        maintenance: "70000",
        areaSqm: "33.5",
        contractMonths: "12",
        commuteMinutes: "38",
        commuteCost: "62000",
        riskScore: "20",
        riskBasis: "사용자 확인: 등기·특약 추가 확인 필요",
      },
      {
        ...candidate("candidate-b"),
        label: "성수동 작은집",
        district: "서울 성동구",
        deposit: "15000000",
        monthlyRent: "620000",
        maintenance: "55000",
        areaSqm: "29",
        contractMonths: "12",
        commuteMinutes: "22",
        commuteCost: "65000",
        riskScore: "10",
        riskBasis: "사용자 확인: 현재 발견한 위험신호 없음",
      },
    ]);
    setError(null);
  }

  async function startSession(event: FormEvent) {
    event.preventDefault();
    if (!consent.privacy || !consent.sensitive) {
      setError({
        message: "두 안내에 모두 동의해야 분석을 시작할 수 있습니다.",
        recovery: "각 안내를 읽고 체크한 뒤 다시 눌러 주세요.",
      });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await requestJson<Session>(
        "/sessions",
        {
          method: "POST",
          body: JSON.stringify({
            consent_version: "privacy-v1",
            privacy_notice_accepted: true,
            sensitive_data_notice_accepted: true,
          }),
        },
        "API 서버가 실행 중인지 확인하고 다시 시도해 주세요.",
      );
      setSession(created);
      setStep("profile");
    } catch (cause) {
      showError(cause, "API 서버를 확인하고 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function saveProfile(event: FormEvent) {
    event.preventDefault();
    const errors = validateProfile(profile);
    if (errors.length) {
      setError({ message: errors[0], recovery: "표시된 값을 수정한 뒤 다시 진행해 주세요." });
      return;
    }
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      await requestJson(
        `/sessions/${session.session.id}/profile`,
        {
          method: "PUT",
          headers: { "X-Session-Token": session.access_token },
          body: JSON.stringify({
            age_years: Number(profile.ageYears),
            monthly_net_income: moneyValue(profile.monthlyIncome),
            liquid_assets: moneyValue(profile.liquidAssets || "0"),
            available_deposit: moneyValue(profile.availableDeposit),
            household_type: profile.householdType,
            is_homeless: profile.isHomeless,
            workplace_district: profile.workplaceDistrict || null,
          }),
        },
        "소득·보증금·나이 값을 확인한 뒤 다시 시도해 주세요.",
      );
      setStep("candidates");
    } catch (cause) {
      showError(cause, "입력값을 확인한 뒤 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  function updateCandidate(index: number, key: keyof CandidateForm, value: string) {
    setCandidates((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? { ...item, [key]: value } : item)),
    );
  }

  async function removeCandidate(index: number) {
    if (candidates.length <= 2) {
      setError({
        message: "비교하려면 후보가 최소 두 개 필요합니다.",
        recovery: "후보를 삭제하는 대신 값을 수정해 주세요.",
      });
      return;
    }
    const item = candidates[index];
    if (item.serverId && session) {
      setBusy(true);
      try {
        await requestJson(
          `/sessions/${session.session.id}/candidates/${item.serverId}`,
          { method: "DELETE", headers: { "X-Session-Token": session.access_token } },
          "잠시 후 다시 삭제해 주세요.",
        );
      } catch (cause) {
        showError(cause, "잠시 후 다시 삭제해 주세요.");
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    setCandidates((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  async function saveCandidates(event: FormEvent) {
    event.preventDefault();
    const errors = validateCandidates(candidates);
    if (errors.length) {
      setError({ message: errors[0], recovery: "해당 후보 입력을 수정한 뒤 다시 진행해 주세요." });
      return;
    }
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      const saved: CandidateForm[] = [];
      for (const item of candidates) {
        const path = item.serverId
          ? `/sessions/${session.session.id}/candidates/${item.serverId}`
          : `/sessions/${session.session.id}/candidates`;
        const response = await requestJson<{ id: string }>(
          path,
          {
            method: item.serverId ? "PUT" : "POST",
            headers: { "X-Session-Token": session.access_token },
            body: JSON.stringify(candidatePayload(item)),
          },
          "후보의 금액·면적·통근 정보를 확인해 주세요.",
        );
        saved.push({ ...item, serverId: response.id });
      }
      setCandidates(saved);
      setResult(null);
      setStep("document");
    } catch (cause) {
      showError(cause, "후보 입력을 확인한 뒤 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDocument() {
    if (!session || !documentFile) {
      setStep("analysis");
      return;
    }
    setBusy(true);
    setProgress("문서 형식과 크기를 확인하고 있습니다.");
    setError(null);
    try {
      const form = new FormData();
      form.append("file", documentFile);
      const uploaded = await requestJson<{ id: string }>(
        `/sessions/${session.session.id}/documents`,
        {
          method: "POST",
          headers: { "X-Session-Token": session.access_token },
          body: form,
        },
        "PDF·PNG·JPEG 중 10MB 이하 파일인지 확인해 주세요. 문서 없이도 진행할 수 있습니다.",
      );
      setProgress("로컬 문서 추출을 실행하고 있습니다.");
      const analysis = await requestJson<DocumentAnalysis>(
        `/sessions/${session.session.id}/documents/${uploaded.id}/extract`,
        { method: "POST", headers: { "X-Session-Token": session.access_token } },
        "OCR을 사용할 수 없으면 후보를 직접 입력한 상태로 계속 진행해 주세요.",
      );
      setDocumentAnalysis(analysis);
      setStep("analysis");
    } catch (cause) {
      showError(cause, "문서 없이 계속 진행하거나 파일을 바꿔 다시 시도해 주세요.");
    } finally {
      setBusy(false);
      setProgress("입력을 기다리고 있습니다.");
    }
  }

  function rankingRequest(
    costs: Record<string, CostResponse>,
    preset: WeightPreset,
  ): { candidates: RankingCandidate[]; weights: (typeof WEIGHT_PRESETS)[WeightPreset] } {
    const reference = new Date().toISOString();
    const rankingCandidates = candidates.map((item) => {
      const id = item.serverId ?? item.localId;
      const cost = costs[id];
      const base = baseScenario(cost);
      return {
        candidate_id: id,
        label: item.label,
        district: item.district,
        monthly_effective_cost: base.breakdown.monthly_effective_cost.amount,
        cost_scenario: "base" as const,
        cost_calculation_version: cost.calculation_version,
        cost_input_sha256: cost.input_sha256,
        commute_minutes: item.commuteMinutes,
        commute_reference_at: reference,
        deposit: item.deposit,
        area_sqm: item.areaSqm,
        risk_score: item.riskScore,
        risk_basis: item.riskBasis,
        policy_statuses: ["OFFICIAL_CHECK_NEEDED"],
        policy_versions: ["seoul-rent-2026-draft-v1"],
      };
    });
    return { candidates: rankingCandidates, weights: WEIGHT_PRESETS[preset] };
  }

  async function analyze() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setProgress("후보별 월평균 비용과 계약기간 총비용을 계산하고 있습니다.");
      const costs: Record<string, CostResponse> = {};
      const costRequests: Array<{ candidate_id: string; input: ReturnType<typeof costPayload> }> = [];
      for (const item of candidates) {
        const id = item.serverId ?? item.localId;
        const input = costPayload(profile, item);
        costs[id] = await requestJson<CostResponse>(
          "/costs/calculate",
          { method: "POST", body: JSON.stringify(input) },
          "관리비, 통근비, 초기비용과 금리를 확인해 주세요.",
        );
        costRequests.push({ candidate_id: id, input });
      }
      setProgress("사용자가 선택한 가중치로 후보를 비교하고 있습니다.");
      const rankingPayload = rankingRequest(costs, weightPreset);
      const ranking = await requestJson<RankingResponse>(
        "/rankings/compare",
        { method: "POST", body: JSON.stringify(rankingPayload) },
        "위험신호 근거와 모든 후보의 비교값을 확인해 주세요.",
      );
      setProgress("정책 근거와 최종 검증 게이트를 확인하고 있습니다.");
      const first = candidates[0];
      const run = await requestJson<AgentRun>(
        `/sessions/${session.session.id}/agent-runs`,
        {
          method: "POST",
          headers: { "X-Session-Token": session.access_token },
          body: JSON.stringify({
            context: {
              use_manual_candidate_entry: true,
              policy_code: "seoul_youth_monthly_rent_2026",
              eligibility_input: {
                as_of_date: new Date().toISOString().slice(0, 10),
                age_years: Number(profile.ageYears),
                region: "서울특별시",
                median_income_ratio_percent: profile.medianIncomeRatio,
                is_homeless: profile.isHomeless,
                deposit: first.deposit,
                monthly_rent: first.monthlyRent,
                received_same_seoul_support_before: profile.receivedSeoulSupport,
                receiving_other_monthly_rent_support: profile.receivingOtherSupport,
              },
              cost_requests: costRequests,
              ranking_request: rankingPayload,
            },
          }),
        },
        "세션이 만료됐다면 처음부터 다시 시작해 주세요.",
      );
      const agent = await requestJson<AgentRun>(
        `/sessions/${session.session.id}/agent-runs/${run.id}/advance`,
        {
          method: "POST",
          headers: { "X-Session-Token": session.access_token },
          body: JSON.stringify({ auto_run: true }),
        },
        "입력값을 수정한 뒤 분석을 다시 실행해 주세요.",
      );
      setResult({
        costs,
        ranking,
        rankingCandidates: rankingPayload.candidates,
        agent,
        analyzedAt: new Date().toISOString(),
      });
      setStep("result");
    } catch (cause) {
      showError(cause, "입력 단계로 돌아가 값을 확인한 뒤 다시 분석해 주세요.");
    } finally {
      setBusy(false);
      setProgress("분석을 완료했습니다.");
    }
  }

  async function recalculate(preset: WeightPreset) {
    if (!result) return;
    setWeightPreset(preset);
    setBusy(true);
    setError(null);
    try {
      const payload = rankingRequest(result.costs, preset);
      const ranking = await requestJson<RankingResponse>(
        "/rankings/compare",
        { method: "POST", body: JSON.stringify(payload) },
        "가중치를 기본값으로 되돌린 뒤 다시 시도해 주세요.",
      );
      setResult({ ...result, ranking, rankingCandidates: payload.candidates, analyzedAt: new Date().toISOString() });
    } catch (cause) {
      showError(cause, "가중치를 기본값으로 되돌린 뒤 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function downloadData() {
    if (!session) return;
    setBusy(true);
    try {
      const exported = await requestJson<object>(
        `/sessions/${session.session.id}/export`,
        { headers: { "X-Session-Token": session.access_token } },
        "세션이 만료됐다면 새 분석을 시작해 주세요.",
      );
      const blob = new Blob([JSON.stringify(exported, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = window.document.createElement("a");
      anchor.href = url;
      anchor.download = downloadFileName();
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (cause) {
      showError(cause, "세션 상태를 확인한 뒤 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteData() {
    if (!session || !window.confirm("입력, 문서와 분석 기록을 모두 삭제할까요? 이 작업은 되돌릴 수 없습니다.")) return;
    setBusy(true);
    try {
      await requestJson(
        `/sessions/${session.session.id}`,
        { method: "DELETE", headers: { "X-Session-Token": session.access_token } },
        "삭제되지 않았다면 잠시 후 다시 시도해 주세요.",
      );
      setSession(null);
      setProfile(emptyProfile);
      setCandidates([candidate("candidate-a", "후보 A"), candidate("candidate-b", "후보 B")]);
      setDocumentAnalysis(null);
      setDocumentFile(null);
      setResult(null);
      setConsent({ privacy: false, sensitive: false });
      setStep("intro");
    } catch (cause) {
      showError(cause, "삭제되지 않았다면 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">본문으로 바로가기</a>
      <header className="topbar">
        <a className="brand" href="#main-content" aria-label="집결정 AI 홈">
          <span className="brand-mark" aria-hidden="true">집</span>
          <span>집결정 AI</span>
        </a>
        <div className="topbar-meta">
          <span className="privacy-chip">24시간 후 자동 삭제</span>
          <span className="local-chip">로컬 AI · 무료</span>
        </div>
      </header>

      <nav className="step-nav" aria-label="분석 진행 단계">
        <ol>
          {STEPS.map((item, index) => (
            <li key={item.id} className={index <= currentStepIndex ? "is-reached" : ""}>
              <span className="step-dot" aria-hidden="true">{index < currentStepIndex ? "✓" : index + 1}</span>
              <span aria-current={item.id === step ? "step" : undefined}>{item.label}</span>
            </li>
          ))}
        </ol>
      </nav>

      <main id="main-content" className="workspace">
        {error ? (
          <section className="error-banner" role="alert">
            <div><strong>{error.message}</strong><p>{error.recovery}</p></div>
            <button type="button" className="text-button" onClick={() => setError(null)}>닫기</button>
          </section>
        ) : null}

        {step === "intro" ? (
          <section className="hero-grid">
            <div className="hero-copy">
              <p className="eyebrow">청년 주거 의사결정 도우미</p>
              <h1 ref={headingRef} tabIndex={-1}>월세가 아니라,<br /><em>내 삶의 총비용</em>으로 비교하세요.</h1>
              <p className="hero-description">
                보증금의 기회비용, 관리비, 통근시간과 정책 조건까지 한 화면에서 확인합니다.
                결과마다 계산식과 출처를 남깁니다.
              </p>
              <div className="trust-row" aria-label="서비스 특징">
                <span>✓ 유료 API 없음</span><span>✓ 근거 없는 추천 차단</span><span>✓ 즉시 삭제 가능</span>
              </div>
            </div>
            <form className="consent-card" onSubmit={startSession}>
              <div className="card-heading"><span className="card-number">01</span><div><p>시작하기</p><h2>데이터 사용 동의</h2></div></div>
              <label className="check-row">
                <input type="checkbox" checked={consent.privacy} onChange={(event) => setConsent({ ...consent, privacy: event.target.checked })} />
                <span><strong>개인정보 처리 안내를 확인했습니다.</strong><small>입력은 익명 세션에 최대 24시간 보관됩니다.</small></span>
              </label>
              <label className="check-row">
                <input type="checkbox" checked={consent.sensitive} onChange={(event) => setConsent({ ...consent, sensitive: event.target.checked })} />
                <span><strong>민감정보 입력 주의를 확인했습니다.</strong><small>주민번호·계좌번호는 입력하지 마세요.</small></span>
              </label>
              <button className="primary-button" disabled={busy} type="submit">{busy ? "세션 만드는 중…" : "내 조건 입력하기"}<span aria-hidden="true">→</span></button>
              <button className="secondary-button" type="button" onClick={fillDemo}>심사용 데모 데이터 채우기</button>
              <p className="legal-note">정책 선정, 대출 승인 또는 계약 안전을 보장하지 않습니다.</p>
            </form>
          </section>
        ) : null}

        {step === "profile" ? (
          <form className="content-card" onSubmit={saveProfile}>
            <div className="section-heading"><div><p className="eyebrow">STEP 2 · 내 조건</p><h1 ref={headingRef} tabIndex={-1}>감당할 수 있는 범위를 알려주세요.</h1><p>값은 결과 카드의 계산 가정에서 다시 확인할 수 있습니다.</p></div><button type="button" className="secondary-button compact" onClick={fillDemo}>데모 값 채우기</button></div>
            <div className="form-grid three">
              <Field label="나이" name="age" type="number" min="19" max="100" value={profile.ageYears} onChange={(e) => setProfile({ ...profile, ageYears: e.target.value })} suffix="세" required />
              <Field label="월 실수령액" name="income" type="number" min="1" value={profile.monthlyIncome} onChange={(e) => setProfile({ ...profile, monthlyIncome: e.target.value })} suffix="원" required />
              <Field label="사용 가능한 보증금" name="availableDeposit" type="number" min="0" value={profile.availableDeposit} onChange={(e) => setProfile({ ...profile, availableDeposit: e.target.value })} suffix="원" required />
              <Field label="현재 유동자산" name="assets" type="number" min="0" value={profile.liquidAssets} onChange={(e) => setProfile({ ...profile, liquidAssets: e.target.value })} suffix="원" />
              <Field label="기준 중위소득 비율" name="median" type="number" min="1" value={profile.medianIncomeRatio} onChange={(e) => setProfile({ ...profile, medianIncomeRatio: e.target.value })} suffix="%" hint="모르면 복지로 모의계산 결과를 입력하세요." required />
              <Field label="예상 대출금리" name="rate" type="number" min="0" step="0.1" value={profile.annualRate} onChange={(e) => setProfile({ ...profile, annualRate: e.target.value })} suffix="%" hint="은행에서 확인한 값을 입력하세요." required />
              <Field label="이사 초기비용" name="initial" type="number" min="0" value={profile.initialCosts} onChange={(e) => setProfile({ ...profile, initialCosts: e.target.value })} suffix="원" />
              <Field label="직장·학교 지역" name="workplace" value={profile.workplaceDistrict} onChange={(e) => setProfile({ ...profile, workplaceDistrict: e.target.value })} placeholder="예: 서울 중구" />
              <label className="field"><span className="field-label">가구 유형</span><span className="input-shell"><select value={profile.householdType} onChange={(e) => setProfile({ ...profile, householdType: e.target.value as ProfileForm["householdType"] })}><option value="single">1인 가구</option><option value="couple">부부</option><option value="single_parent">한부모</option><option value="other">기타</option></select></span></label>
            </div>
            <fieldset className="choice-panel"><legend>정책 확인 정보</legend><label><input type="checkbox" checked={profile.isHomeless} onChange={(e) => setProfile({ ...profile, isHomeless: e.target.checked })} /> 현재 무주택입니다.</label><label><input type="checkbox" checked={profile.receivedSeoulSupport} onChange={(e) => setProfile({ ...profile, receivedSeoulSupport: e.target.checked })} /> 이전 서울시 청년월세지원 수급 이력이 있습니다.</label><label><input type="checkbox" checked={profile.receivingOtherSupport} onChange={(e) => setProfile({ ...profile, receivingOtherSupport: e.target.checked })} /> 현재 다른 월세지원을 받고 있습니다.</label></fieldset>
            <div className="form-actions"><button type="button" className="secondary-button" onClick={() => setStep("intro")}>이전</button><button className="primary-button" disabled={busy} type="submit">후보 입력하기 <span aria-hidden="true">→</span></button></div>
          </form>
        ) : null}

        {step === "candidates" ? (
          <form className="content-card wide" onSubmit={saveCandidates}>
            <div className="section-heading"><div><p className="eyebrow">STEP 3 · 후보 주택</p><h1 ref={headingRef} tabIndex={-1}>비교할 집을 나란히 입력하세요.</h1><p>위험점수는 자동 생성하지 않습니다. 직접 확인한 신호와 근거를 입력하세요.</p></div><button type="button" className="secondary-button compact" disabled={candidates.length >= 3} onClick={() => setCandidates([...candidates, candidate(`candidate-${Date.now()}`, `후보 ${String.fromCharCode(65 + candidates.length)}`)])}>+ 후보 추가</button></div>
            <div className="candidate-grid">
              {candidates.map((item, index) => (
                <fieldset className="candidate-card" key={item.localId}>
                  <legend><span>{String.fromCharCode(65 + index)}</span> {item.label || `후보 ${index + 1}`}</legend>
                  <button type="button" className="remove-button" onClick={() => void removeCandidate(index)} aria-label={`${item.label || `후보 ${index + 1}`} 삭제`}>삭제</button>
                  <div className="form-grid two">
                    <Field label="후보 이름" name={`label-${index}`} value={item.label} onChange={(e) => updateCandidate(index, "label", e.target.value)} required />
                    <Field label="지역" name={`district-${index}`} value={item.district} onChange={(e) => updateCandidate(index, "district", e.target.value)} placeholder="서울 마포구" required />
                    <Field label="보증금" name={`deposit-${index}`} type="number" min="0" value={item.deposit} onChange={(e) => updateCandidate(index, "deposit", e.target.value)} suffix="원" required />
                    <Field label="월세" name={`rent-${index}`} type="number" min="0" value={item.monthlyRent} onChange={(e) => updateCandidate(index, "monthlyRent", e.target.value)} suffix="원" required />
                    <Field label="관리비" name={`maintenance-${index}`} type="number" min="0" value={item.maintenance} onChange={(e) => updateCandidate(index, "maintenance", e.target.value)} suffix="원" required />
                    <Field label="전용면적" name={`area-${index}`} type="number" min="1" step="0.1" value={item.areaSqm} onChange={(e) => updateCandidate(index, "areaSqm", e.target.value)} suffix="㎡" required />
                    <Field label="계약기간" name={`months-${index}`} type="number" min="1" max="120" value={item.contractMonths} onChange={(e) => updateCandidate(index, "contractMonths", e.target.value)} suffix="개월" required />
                    <Field label="편도 통근시간" name={`commute-${index}`} type="number" min="0" value={item.commuteMinutes} onChange={(e) => updateCandidate(index, "commuteMinutes", e.target.value)} suffix="분" required />
                    <Field label="월 통근비" name={`commute-cost-${index}`} type="number" min="0" value={item.commuteCost} onChange={(e) => updateCandidate(index, "commuteCost", e.target.value)} suffix="원" required />
                    <Field label="주의 신호 점수" name={`risk-${index}`} type="number" min="0" max="100" value={item.riskScore} onChange={(e) => updateCandidate(index, "riskScore", e.target.value)} suffix="/ 100" required />
                  </div>
                  <label className="field full"><span className="field-label">주의 신호 근거</span><textarea value={item.riskBasis} onChange={(e) => updateCandidate(index, "riskBasis", e.target.value)} placeholder="예: 등기부는 확인했지만 특약 협의가 필요함" required /></label>
                </fieldset>
              ))}
            </div>
            <div className="form-actions"><button type="button" className="secondary-button" onClick={() => setStep("profile")}>이전</button><button className="primary-button" disabled={busy} type="submit">문서 확인하기 <span aria-hidden="true">→</span></button></div>
          </form>
        ) : null}

        {step === "document" ? (
          <section className="content-card document-layout">
            <div><p className="eyebrow">STEP 4 · 선택 입력</p><h1 ref={headingRef} tabIndex={-1}>계약서가 있다면 대조해 볼까요?</h1><p className="lead">문서 없이도 직접 입력한 후보 정보로 계속 진행할 수 있습니다. 로컬 OCR이 준비되지 않았다면 자동으로 수동 경로를 안내합니다.</p><div className="upload-zone"><label htmlFor="lease-file"><span className="upload-icon" aria-hidden="true">⇧</span><strong>{documentFile ? documentFile.name : "PDF·PNG·JPEG 파일 선택"}</strong><small>최대 10MB · 원본은 익명 세션 종료 시 삭제</small></label><input id="lease-file" type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => setDocumentFile(e.target.files?.[0] ?? null)} /></div></div>
            <aside className="privacy-panel"><h2>문서 처리 원칙</h2><ul><li><span>1</span> 주민번호·연락처 자동 마스킹</li><li><span>2</span> 추출값은 사용자 확인 전 계산 제외</li><li><span>3</span> 문서 속 AI 명령은 실행하지 않음</li></ul></aside>
            <div className="form-actions span-all"><button type="button" className="secondary-button" onClick={() => setStep("candidates")}>이전</button><button className="primary-button" disabled={busy} type="button" onClick={() => void handleDocument()}>{busy ? progress : documentFile ? "업로드하고 확인" : "문서 없이 계속"} <span aria-hidden="true">→</span></button></div>
          </section>
        ) : null}

        {step === "analysis" ? (
          <section className="content-card analysis-layout">
            <div><p className="eyebrow">STEP 5 · 분석 준비</p><h1 ref={headingRef} tabIndex={-1}>확정한 값만 계산에 사용합니다.</h1><p className="lead">아래 입력과 가정을 확인하세요. 정책 원문은 아직 팀원 교차검토 전이라 최종 결과에 ‘공식 확인 필요’가 표시됩니다.</p></div>
            {documentAnalysis ? (
              <section className="document-result" aria-labelledby="document-result-title">
                <div className="status-line">
                  <span className={documentAnalysis.document.status === "extracted" ? "status-ok" : "status-warn"}>{documentAnalysis.document.status === "extracted" ? "✓ 추출 완료" : "! 수동 입력 사용"}</span>
                  {documentAnalysis.injection_detected ? <span className="status-warn">! 문서 지시문 차단</span> : null}
                </div>
                <h2 id="document-result-title">문서 추출값</h2>
                {documentAnalysis.fields.length ? (
                  <dl className="extracted-grid">{documentAnalysis.fields.map((field) => <div key={field.name}><dt>{field.name}</dt><dd>{field.normalized_value}</dd><small>신뢰도 {field.confidence ? percent(Number(field.confidence) * 100) : "수동 확인"}</small></div>)}</dl>
                ) : <p>자동 추출을 사용할 수 없어 후보 폼의 직접 입력값으로 진행합니다.</p>}
                <p className="hint">추출값은 참고용이며 자동 반영되지 않습니다. 후보 입력값과 대조한 뒤 직접 확정하세요.</p>
                <button type="button" className="secondary-button" onClick={() => setStep("candidates")}>후보 입력과 대조·수정</button>
              </section>
            ) : <section className="document-result"><span className="status-neutral">문서 없음 · 직접 입력</span><p>후보 단계에서 입력한 확정값만 사용합니다.</p></section>}
            <div className="assumption-strip"><div><span>금리</span><strong>연 {profile.annualRate}%</strong><small>사용자 입력값 고정</small></div><div><span>자기자금 기회비용</span><strong>0%</strong><small>현재 MVP 기본값</small></div><div><span>정책지원금</span><strong>0원 반영</strong><small>선정 전이므로 제외</small></div><div><span>비교 가중치</span><strong>비용 40 · 통근 25</strong><small>결과에서 변경 가능</small></div></div>
            <div className="analysis-steps" aria-live="polite"><div className={busy ? "is-running" : ""}><span>1</span><p><strong>주거비 계산</strong>월평균·계약 총비용</p></div><div><span>2</span><p><strong>정책 근거 확인</strong>기준일·출처·검토상태</p></div><div><span>3</span><p><strong>후보 비교</strong>기여도·민감도·검증</p></div></div>
            <p className="sr-only" aria-live="assertive">{busy ? progress : "분석 시작 준비가 됐습니다."}</p>
            <div className="form-actions"><button type="button" className="secondary-button" onClick={() => setStep("document")}>이전</button><button type="button" className="primary-button" disabled={busy} onClick={() => void analyze()}>{busy ? "분석 중…" : "검증 가능한 분석 시작"} <span aria-hidden="true">→</span></button></div>
          </section>
        ) : null}

        {step === "result" && result ? (
          <ResultView
            result={result}
            candidates={candidates}
            winner={winner}
            profile={profile}
            weightPreset={weightPreset}
            busy={busy}
            headingRef={headingRef}
            onPreset={(preset) => void recalculate(preset)}
            onEdit={() => setStep("candidates")}
            onDownload={() => void downloadData()}
            onDelete={() => void deleteData()}
          />
        ) : null}
      </main>
      <footer className="footer"><span>집결정 AI · 설명 가능한 주거비 비교</span><span>정책·대출·계약의 최종 판단은 공식기관에서 확인하세요.</span></footer>
    </div>
  );
}

function ResultView({
  result,
  candidates,
  winner,
  profile,
  weightPreset,
  busy,
  headingRef,
  onPreset,
  onEdit,
  onDownload,
  onDelete,
}: {
  result: ResultBundle;
  candidates: CandidateForm[];
  winner: CandidateForm | null;
  profile: ProfileForm;
  weightPreset: WeightPreset;
  busy: boolean;
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  onPreset: (preset: WeightPreset) => void;
  onEdit: () => void;
  onDownload: () => void;
  onDelete: () => void;
}) {
  const ranked = [...result.ranking.results].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  const winnerRank = ranked[0];
  const winnerCost = winnerRank ? result.costs[winnerRank.candidate_id] : null;
  const base = winnerCost ? baseScenario(winnerCost) : null;
  const biggest = winnerRank?.contributions.reduce((best, item) => Number(item.contribution) > Number(best.contribution) ? item : best, winnerRank.contributions[0]);
  const official = result.agent.state !== "decision_card";
  const checklist = result.agent.decision_card?.checklist ?? [];

  return (
    <section className="result-page">
      <div className="result-hero">
        <div><p className="eyebrow">STEP 6 · 의사결정 카드</p><h1 ref={headingRef} tabIndex={-1}>{winner ? <><em>{winner.label}</em>이 현재 조건에서<br />가장 균형이 좋습니다.</> : "비교 결과를 확인하세요."}</h1><p>점수 하나가 아니라 비용, 통근, 보증금, 면적과 주의 신호의 기여도를 함께 보여드립니다.</p></div>
        <div className={official ? "official-badge warning" : "official-badge success"}><span aria-hidden="true">{official ? "!" : "✓"}</span><div><strong>{official ? "공식 확인 필요" : "검증 게이트 통과"}</strong><small>{official ? "정책 원문·규칙 교차검토 전" : "모든 근거와 계산 검증 완료"}</small></div></div>
      </div>

      <div className="metric-grid">
        <article><span>월평균 실질 주거비</span><strong>{base ? money(base.breakdown.monthly_effective_cost.amount) : "—"}</strong><small>월세·관리비·대출비용·통근비 포함</small></article>
        <article><span>계약기간 총비용</span><strong>{base ? money(base.breakdown.contract_total_cost.amount) : "—"}</strong><small>환급되는 보증금 원금 제외</small></article>
        <article><span>소득 대비 부담률</span><strong>{base ? percent(base.breakdown.housing_cost_burden_percent) : "—"}</strong><small>월 실수령액 {money(profile.monthlyIncome)} 기준</small></article>
        <article><span>가장 큰 순위 기준</span><strong>{biggest ? contributionLabel(biggest.criterion) : "—"}</strong><small>기여도 {biggest ? biggest.contribution : "—"}점</small></article>
      </div>

      <section className="dashboard-section" aria-labelledby="comparison-title">
        <div className="section-heading compact-heading"><div><p className="eyebrow">후보 비교</p><h2 id="comparison-title">숫자를 펼쳐서 비교하세요.</h2></div><button className="secondary-button compact" type="button" onClick={onEdit}>후보 수정</button></div>
        <div className="comparison-table-wrap"><table className="comparison-table"><caption className="sr-only">후보별 순위, 월평균 비용, 총비용, 통근시간, 보증금 비교</caption><thead><tr><th scope="col">후보</th><th scope="col">순위</th><th scope="col">월평균</th><th scope="col">계약 총비용</th><th scope="col">통근</th><th scope="col">보증금</th><th scope="col">주의</th></tr></thead><tbody>{ranked.map((rank) => { const item = candidates.find((candidateItem) => candidateItem.serverId === rank.candidate_id); const itemCost = result.costs[rank.candidate_id]; const itemBase = baseScenario(itemCost); return <tr key={rank.candidate_id} className={rank.rank === 1 ? "winner-row" : ""}><th scope="row"><span className="candidate-swatch" aria-hidden="true" />{rank.label}</th><td><strong>{rank.rank ? `${rank.rank}위` : "보류"}</strong></td><td>{money(itemBase.breakdown.monthly_effective_cost.amount)}</td><td>{money(itemBase.breakdown.contract_total_cost.amount)}</td><td>{item?.commuteMinutes ?? "—"}분</td><td>{money(item?.deposit ?? "")}</td><td>{item?.riskScore ?? "—"}/100</td></tr>; })}</tbody></table></div>
      </section>

      <div className="result-columns">
        <section className="dashboard-section"><p className="eyebrow">왜 이 순위인가요?</p><h2>기준별 점수 기여도</h2><div className="contribution-list">{winnerRank?.contributions.map((item) => <div key={item.criterion}><div><span>{contributionLabel(item.criterion)}</span><strong>{item.contribution}점</strong></div><div className="bar-track"><span style={{ width: `${Math.min(100, Number(item.contribution) * 2.5)}%` }} /></div><small>원값 {item.raw_value} · 가중치 {item.weight_percent}%</small></div>)}</div><details><summary>장점·단점과 비교 근거 보기</summary><ul className="detail-list">{result.ranking.tradeoffs.filter((item) => item.candidate_id === winnerRank?.candidate_id).map((tradeoff) => <li key={tradeoff.compared_with_id}><strong>{candidates.find((item) => item.serverId === tradeoff.compared_with_id)?.label ?? "다른 후보"} 대비</strong> 장점: {tradeoff.advantages.map(contributionLabel).join(", ") || "없음"} · 단점: {tradeoff.disadvantages.map(contributionLabel).join(", ") || "없음"}</li>)}</ul></details></section>
        <section className="dashboard-section simulator"><p className="eyebrow">조건 변경 시뮬레이터</p><h2>무엇을 더 중요하게 볼까요?</h2><div className="preset-buttons" role="group" aria-label="비교 가중치 선택"><button type="button" aria-pressed={weightPreset === "balanced"} onClick={() => onPreset("balanced")}>균형형<small>비용 40 · 통근 25</small></button><button type="button" aria-pressed={weightPreset === "cost"} onClick={() => onPreset("cost")}>비용 우선<small>비용 60 · 통근 15</small></button><button type="button" aria-pressed={weightPreset === "commute"} onClick={() => onPreset("commute")}>통근 우선<small>비용 30 · 통근 45</small></button></div><div className="sensitivity-note"><span aria-hidden="true">↺</span><p><strong>{result.ranking.sensitivity?.winner_changes ? "조건에 따라 1위가 달라집니다." : "현재 범위에서는 1위가 안정적입니다."}</strong>가중치를 바꾸면 서버에서 순위를 다시 계산합니다.</p></div>{busy ? <p aria-live="polite">순위를 다시 계산하고 있습니다…</p> : null}</section>
      </div>

      <section className="dashboard-section policy-section"><div><p className="eyebrow">정책 판정</p><h2>서울시 청년월세지원</h2><span className="status-warn">! 공식 확인 필요</span><p>현재 원문 스냅샷과 규칙은 팀원 교차검토 전입니다. 예상 지원금을 비용에서 차감하지 않았습니다.</p></div><div className="gate-list">{result.agent.verification_gates.map((gate) => <div key={gate.code}><span aria-hidden="true">{gate.passed ? "✓" : "!"}</span><p><strong>{gate.code}</strong>{gate.reason}</p></div>)}</div></section>

      <section className="dashboard-section"><p className="eyebrow">계약 전 확인</p><h2>근거가 연결된 체크리스트</h2>{checklist.length ? <div className="checklist">{checklist.map((item) => <details key={item.code}><summary><span aria-hidden="true">□</span><span><strong>{item.action}</strong><small>{item.verification_actor} 확인 · {item.version}</small></span></summary><p>{item.disclaimer}</p>{item.citations.map((citation) => <a key={citation.id} href={citation.url} target="_blank" rel="noreferrer">{citation.institution} · {citation.locator} <span aria-hidden="true">↗</span></a>)}</details>)}</div> : <p className="empty-note">검토된 체크리스트가 없어 공식기관 확인이 필요합니다.</p>}</section>

      <section className="dashboard-section evidence-section"><p className="eyebrow">계산과 출처 상세</p><h2>결과를 직접 검증할 수 있습니다.</h2><div className="details-grid"><details open><summary>계산 가정</summary><dl><div><dt>대출금리</dt><dd>연 {profile.annualRate}%</dd></div><div><dt>자기자금 기회비용</dt><dd>0%</dd></div><div><dt>정책지원금</dt><dd>선정 전이므로 미반영</dd></div><div><dt>분석 기준시각</dt><dd>{new Date(result.analyzedAt).toLocaleString("ko-KR")}</dd></div></dl></details><details><summary>서비스 한계</summary><p>{result.agent.decision_card?.disclaimer ?? "이 결과는 비교 자료이며 정책 선정, 대출 승인 또는 계약 안전을 보장하지 않습니다."}</p></details><details><summary>오류·경고</summary><ul className="detail-list">{[...result.ranking.warnings, ...result.agent.official_check_reasons].map((warning) => <li key={warning}>{warning}</li>)}</ul></details></div></section>

      <div className="result-actions"><button type="button" className="secondary-button" onClick={onDownload}>내 데이터 JSON 다운로드</button><button type="button" className="danger-button" onClick={onDelete}>세션과 데이터 삭제</button></div>
    </section>
  );
}
