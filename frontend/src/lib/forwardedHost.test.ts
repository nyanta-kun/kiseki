import { describe, expect, it } from "vitest";

import { restoreForwardedUrl } from "./forwardedHost";

function headers(values: Record<string, string>) {
  return {
    get: (name: string) => values[name.toLowerCase()] ?? null,
  };
}

describe("restoreForwardedUrl", () => {
  it("X-Forwarded-Host が無ければ何もしない", () => {
    // ローカル開発やプロキシを通らない経路では req.url が既に正しい。
    const url = "http://localhost:3000/api/auth/providers";
    expect(restoreForwardedUrl(url, headers({}))).toBe(url);
  });

  it("バインドアドレスを実ホストへ差し替える", () => {
    // 🔴 これが 2026-09-09 に本番を壊した形。コンテナ内の Next.js は
    //    req.url を 0.0.0.0:3000 で組み立てる。
    const got = restoreForwardedUrl(
      "https://0.0.0.0:3000/api/auth/providers",
      headers({ "x-forwarded-host": "galloplab.com", "x-forwarded-proto": "https" }),
    );
    expect(got).toBe("https://galloplab.com/api/auth/providers");
  });

  it("🔴 ポートを残さない", () => {
    // url.host への代入はポートを消さない（WHATWG URL の仕様）。
    // これを踏むと callbackUrl が https://galloplab.com:3000/... になる。
    const got = restoreForwardedUrl(
      "http://localhost:3000/api/auth/callback/google",
      headers({ "x-forwarded-host": "galloplab.com", "x-forwarded-proto": "https" }),
    );
    expect(got).not.toContain(":3000");
    expect(got).toBe("https://galloplab.com/api/auth/callback/google");
  });

  it("ホストごとに別の URL になる（ブランド併存の要件）", () => {
    const path = "https://0.0.0.0:3000/api/auth/signin/google";
    const a = restoreForwardedUrl(path, headers({ "x-forwarded-host": "galloplab.com" }));
    const b = restoreForwardedUrl(
      path,
      headers({ "x-forwarded-host": "sekito-stable.com" }),
    );
    expect(a).toBe("https://galloplab.com/api/auth/signin/google");
    expect(b).toBe("https://sekito-stable.com/api/auth/signin/google");
  });

  it("proto が無ければ https とみなす", () => {
    const got = restoreForwardedUrl(
      "http://0.0.0.0:3000/api/auth/csrf",
      headers({ "x-forwarded-host": "galloplab.com" }),
    );
    expect(got).toBe("https://galloplab.com/api/auth/csrf");
  });

  it("ポート付きの X-Forwarded-Host はそのまま使う", () => {
    const got = restoreForwardedUrl(
      "http://0.0.0.0:3000/api/auth/csrf",
      headers({ "x-forwarded-host": "example.test:8443", "x-forwarded-proto": "https" }),
    );
    expect(got).toBe("https://example.test:8443/api/auth/csrf");
  });

  it("クエリとパスを保つ", () => {
    const got = restoreForwardedUrl(
      "https://0.0.0.0:3000/api/auth/signin?callbackUrl=%2Fraces",
      headers({ "x-forwarded-host": "galloplab.com", "x-forwarded-proto": "https" }),
    );
    expect(got).toBe("https://galloplab.com/api/auth/signin?callbackUrl=%2Fraces");
  });

  it("既に一致していれば入力をそのまま返す", () => {
    const url = "https://galloplab.com/api/auth/providers";
    expect(
      restoreForwardedUrl(
        url,
        headers({ "x-forwarded-host": "galloplab.com", "x-forwarded-proto": "https" }),
      ),
    ).toBe(url);
  });
});
