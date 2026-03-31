import { handlers } from "@/auth";
import { NextRequest } from "next/server";

// Next.js はルートハンドラに渡す req.url から nginx が除去した basePath を除いた形で渡す。
// 例: sekito-stable.com では nginx が /kiseki を除去して /api/auth/callback/google を渡す。
// Auth.js は AUTH_URL のパス(/kiseki/api/auth) を config.basePath として使いアクションを
// 解析するが、/api/auth/... では ^/kiseki/api/auth にマッチせず client 向け URL が壊れる。
// → AUTH_URL からサブパスプレフィックスを抽出し、リクエストに復元する。
//
// AUTH_URL=https://sekito-stable.com/kiseki/api/auth → prefix=/kiseki
// AUTH_URL=https://galloplab.com/auth                → prefix="" (注入不要)
// AUTH_URL=http://localhost:3000/auth                → prefix="" (注入不要)

function getBasepathPrefix(): string {
  const authUrl = process.env.AUTH_URL ?? "";
  if (!authUrl) return "";
  try {
    const path = new URL(authUrl).pathname;
    // /kiseki/api/auth → /kiseki
    // /api/auth        → ""
    // /auth            → ""
    const withoutAuthSuffix = path.replace(/\/api\/auth$/, "").replace(/\/auth$/, "");
    return withoutAuthSuffix;
  } catch {
    return "";
  }
}

const PREFIX = getBasepathPrefix();

function injectBasePath(req: NextRequest): NextRequest {
  if (!PREFIX) return req;
  const url = new URL(req.url);
  if (!url.pathname.startsWith(PREFIX)) {
    url.pathname = PREFIX + url.pathname;
    return new NextRequest(url.toString(), {
      method: req.method,
      headers: req.headers,
      body: req.body,
    });
  }
  return req;
}

export const GET = (req: NextRequest) => handlers.GET(injectBasePath(req));
export const POST = (req: NextRequest) => handlers.POST(injectBasePath(req));
