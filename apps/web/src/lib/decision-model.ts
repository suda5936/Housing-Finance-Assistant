export type ProfileForm = {
  ageYears: string;
  monthlyIncome: string;
  liquidAssets: string;
  availableDeposit: string;
  medianIncomeRatio: string;
  initialCosts: string;
  annualRate: string;
  householdType: "single" | "couple" | "single_parent" | "other";
  workplaceDistrict: string;
  isHomeless: boolean;
  receivedSeoulSupport: boolean;
  receivingOtherSupport: boolean;
};

export type CandidateForm = {
  localId: string;
  serverId?: string;
  label: string;
  district: string;
  deposit: string;
  monthlyRent: string;
  maintenance: string;
  areaSqm: string;
  contractMonths: string;
  commuteMinutes: string;
  commuteCost: string;
  riskScore: string;
  riskBasis: string;
};

export type WeightPreset = "balanced" | "cost" | "commute";

export const WEIGHT_PRESETS = {
  balanced: {
    monthly_effective_cost: "40",
    commute_minutes: "25",
    deposit: "15",
    area_sqm: "10",
    risk_score: "10",
    infrastructure_score: "0",
  },
  cost: {
    monthly_effective_cost: "60",
    commute_minutes: "15",
    deposit: "10",
    area_sqm: "5",
    risk_score: "10",
    infrastructure_score: "0",
  },
  commute: {
    monthly_effective_cost: "30",
    commute_minutes: "45",
    deposit: "10",
    area_sqm: "5",
    risk_score: "10",
    infrastructure_score: "0",
  },
} as const;

export function money(value: string | number): string {
  const amount = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(amount)) return "—";
  return `${new Intl.NumberFormat("ko-KR").format(Math.round(amount))}원`;
}

export function percent(value: string | number): string {
  const amount = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(amount)) return "—";
  return `${amount.toFixed(1)}%`;
}

export function validateProfile(profile: ProfileForm): string[] {
  const errors: string[] = [];
  const age = Number(profile.ageYears);
  if (!Number.isFinite(age) || age < 19 || age > 100) errors.push("나이는 19~100세로 입력해 주세요.");
  if (Number(profile.monthlyIncome) <= 0) errors.push("월 실수령액을 입력해 주세요.");
  if (Number(profile.availableDeposit) < 0) errors.push("사용 가능한 보증금은 0 이상이어야 합니다.");
  if (Number(profile.medianIncomeRatio) <= 0) errors.push("기준 중위소득 비율을 입력해 주세요.");
  if (Number(profile.annualRate) < 0) errors.push("예상 대출금리는 0 이상이어야 합니다.");
  return errors;
}

export function validateCandidates(candidates: CandidateForm[]): string[] {
  const errors: string[] = [];
  if (candidates.length < 2) errors.push("비교할 후보를 두 개 이상 입력해 주세요.");
  if (candidates.length > 3) errors.push("MVP에서는 후보를 세 개까지 비교할 수 있습니다.");
  for (const [index, candidate] of candidates.entries()) {
    const prefix = `후보 ${index + 1}`;
    if (!candidate.label.trim()) errors.push(`${prefix} 이름을 입력해 주세요.`);
    if (!candidate.district.trim()) errors.push(`${prefix} 지역을 입력해 주세요.`);
    if (Number(candidate.deposit) < 0 || Number(candidate.monthlyRent) < 0) {
      errors.push(`${prefix} 보증금과 월세는 0 이상이어야 합니다.`);
    }
    if (Number(candidate.areaSqm) <= 0) errors.push(`${prefix} 전용면적을 입력해 주세요.`);
    if (Number(candidate.contractMonths) < 1) errors.push(`${prefix} 계약기간을 입력해 주세요.`);
    if (Number(candidate.riskScore) < 0 || Number(candidate.riskScore) > 100) {
      errors.push(`${prefix} 위험신호 점수는 0~100이어야 합니다.`);
    }
    if (!candidate.riskBasis.trim()) errors.push(`${prefix} 위험신호 점수의 근거를 입력해 주세요.`);
  }
  return errors;
}

export function housingCostBurden(monthlyCost: number, monthlyIncome: number): number {
  if (monthlyIncome <= 0) return 0;
  return (monthlyCost / monthlyIncome) * 100;
}

export function contributionLabel(criterion: string): string {
  const labels: Record<string, string> = {
    monthly_effective_cost: "실질 주거비",
    commute_minutes: "통근시간",
    deposit: "보증금",
    area_sqm: "전용면적",
    risk_score: "주의 신호",
    infrastructure_score: "생활 인프라",
  };
  return labels[criterion] ?? criterion;
}

export function downloadFileName(date = new Date()): string {
  const day = date.toISOString().slice(0, 10);
  return `homefit-session-${day}.json`;
}
