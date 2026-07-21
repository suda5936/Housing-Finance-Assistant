import { afterEach, describe, expect, it } from "vitest";

import { getApiUrl } from "./api";

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
});
