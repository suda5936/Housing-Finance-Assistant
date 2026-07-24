import { afterEach, describe, expect, it } from "vitest";

import { ApiError, getApiUrl, requestJson } from "./api";

describe("getApiUrl", () => {
  afterEach(() => {
    delete process.env.NEXT_PUBLIC_API_URL;
  });

  it("uses the local API by default", () => {
    expect(getApiUrl("health")).toBe("http://localhost:8000/health");
  });

  it("normalizes a configured base URL and path", () => {
    process.env.NEXT_PUBLIC_API_URL = "https://api.example.com/";

    expect(getApiUrl("/health")).toBe("https://api.example.com/health");
  });

  it("returns structured JSON and preserves recovery guidance", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () =>
      new Response(JSON.stringify({ error: { message: "잘못된 입력" } }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    try {
      await expect(requestJson("/test", {}, "후보를 수정해 주세요.")).rejects.toEqual(
        new ApiError("잘못된 입력", 400, "후보를 수정해 주세요."),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
