"use client";

import { useEffect, useState } from "react";

import { getApiUrl } from "@/lib/api";

type LlmStatus = {
  provider: string;
  model: string;
  state: "ready" | "disabled" | "model_missing" | "unavailable";
  manual_fallback: boolean;
  detail: string;
};

const FALLBACK_STATUS: LlmStatus = {
  provider: "ollama",
  model: "qwen3:4b",
  state: "unavailable",
  manual_fallback: true,
  detail: "API에 연결할 수 없어 수동 입력 모드로 진행합니다.",
};

export function SystemStatus() {
  const [status, setStatus] = useState<LlmStatus | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadStatus() {
      try {
        const response = await fetch(getApiUrl("/system/llm"), { signal: controller.signal });
        if (!response.ok) throw new Error("status request failed");
        setStatus((await response.json()) as LlmStatus);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setStatus(FALLBACK_STATUS);
      }
    }

    void loadStatus();
    return () => controller.abort();
  }, []);

  const ready = status?.state === "ready";

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-900 p-6" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-slate-400">로컬 AI 상태</p>
          <h2 className="mt-1 text-xl font-semibold">{status?.model ?? "확인 중"}</h2>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-sm font-semibold ${
            ready ? "bg-emerald-400/15 text-emerald-300" : "bg-amber-400/15 text-amber-200"
          }`}
        >
          {status === null ? "확인 중" : ready ? "사용 가능" : "수동 모드"}
        </span>
      </div>
      <p className="mt-4 text-sm leading-6 text-slate-300">
        {status?.detail ?? "Ollama와 Qwen3-4B 연결 상태를 확인하고 있습니다."}
      </p>
    </section>
  );
}

