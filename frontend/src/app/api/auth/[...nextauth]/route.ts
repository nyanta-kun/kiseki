import { handlers } from "@/auth";
import { NextRequest } from "next/server";

// Next.js v16 はルートハンドラに渡す req.url から basePath(/kiseki) を削除する。
// Auth.js は AUTH_URL のパス(/kiseki/api/auth) を config.basePath として使い
// アクション解析を行うが、/api/auth/... では ^/kiseki/api/auth にマッチしない。
// → /kiseki プレフィックスを復元することでアクション解析を通す。
const NEXT_BASEPATH = "/kiseki";

function injectBasePath(req: NextRequest): Request {
  const url = new URL(req.url);
  if (!url.pathname.startsWith(NEXT_BASEPATH)) {
    url.pathname = NEXT_BASEPATH + url.pathname;
    return new Request(url.toString(), req);
  }
  return req;
}

export const GET = (req: NextRequest) => handlers.GET(injectBasePath(req));
export const POST = (req: NextRequest) => handlers.POST(injectBasePath(req));
