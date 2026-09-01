import { describe, expect, it, vi, beforeEach } from "vitest";

describe("auth token storage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it("stores and retrieves access token", async () => {
    const { storeTokens, getAccessToken } = await import("./auth");
    storeTokens({
      access_token: "abc",
      refresh_token: "def",
      expires_in: 3600,
      role: "shopper",
      merchant_id: "merchant_keen",
      user_id: "user_1",
    });
    expect(getAccessToken()).toBe("abc");
  });
});
