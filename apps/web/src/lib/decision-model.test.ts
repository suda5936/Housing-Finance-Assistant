import { describe, expect, it } from "vitest";

import {
  WEIGHT_PRESETS,
  downloadFileName,
  housingCostBurden,
  money,
  validateCandidates,
  validateProfile,
} from "./decision-model";

describe("decision model", () => {
  it("formats won and computes housing burden", () => {
    expect(money(1234567)).toBe("1,234,567원");
    expect(housingCostBurden(750000, 3000000)).toBe(25);
  });

  it("keeps every ranking preset at exactly 100", () => {
    for (const preset of Object.values(WEIGHT_PRESETS)) {
      const total = Object.values(preset).reduce((sum, value) => sum + Number(value), 0);
      expect(total).toBe(100);
    }
  });

  it("reports actionable profile and candidate errors", () => {
    const profileErrors = validateProfile({
      ageYears: "18",
      monthlyIncome: "0",
      liquidAssets: "0",
      availableDeposit: "0",
      medianIncomeRatio: "0",
      initialCosts: "0",
      annualRate: "-1",
      householdType: "single",
      workplaceDistrict: "",
      isHomeless: true,
      receivedSeoulSupport: false,
      receivingOtherSupport: false,
    });
    expect(profileErrors).toContain("나이는 19~100세로 입력해 주세요.");
    expect(profileErrors).toContain("월 실수령액을 입력해 주세요.");
    expect(validateCandidates([])).toContain("비교할 후보를 두 개 이상 입력해 주세요.");
  });

  it("creates a stable dated export filename", () => {
    expect(downloadFileName(new Date("2026-07-24T00:00:00Z"))).toBe(
      "homefit-session-2026-07-24.json",
    );
  });
});
