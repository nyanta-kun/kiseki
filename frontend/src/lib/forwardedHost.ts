/**
 * リバースプロキシ越しの実ホストを URL に復元する。
 *
 * ## なぜ要るか（2026-09-09・本番を壊して学んだ）
 *
 * ブランド併存（galloplab.com と sekito-stable.com）のため、Auth.js に
 * ホストごとのコールバック URL を作らせたい。`AUTH_URL` は**単一値**なので、
 * 設定していると別ホストでも galloplab.com を指してしまう。
 *
 * ならば外せばよい、とやったところ**本番のログインが壊れた**:
 *
 *     AUTH_URL を外した直後の https://galloplab.com/api/auth/providers
 *       → callbackUrl https://0.0.0.0:3000/api/auth/callback/google
 *
 * コンテナ内の Next.js は `req.url` を**自分のバインドアドレス**で組み立てており、
 * `trustHost: true` だけでは nginx 越しの実ホストに届かない。nginx に
 * `X-Forwarded-Host` を足しても、Auth.js が見るのは `req.url` なので変わらない。
 *
 * → **`req.url` のホストを `X-Forwarded-Host` で書き換えてから Auth.js へ渡す。**
 *
 * ## ⚠️ ポートは自動では消えない
 *
 * `url.host = "example.com"` は**既存のポートを残す**（WHATWG URL の仕様。
 * ポートを含まない値を代入したときの挙動）。ローカル検証で
 * `https://galloplab.com:3000/...` というコールバックを作ってしまった。
 * ホスト名だけが来たらポートを明示的に空にする。
 *
 * ## ⚠️ ヘッダは詐称されうる
 *
 * `X-Forwarded-Host` はクライアントが送れる。nginx が必ず自分の `$host` で
 * 上書きするので外から持ち込んだ値は届かないが、**nginx を通さずに
 * このポートを直接公開してはいけない**（本番は 127.0.0.1 束縛）。
 */

/** `restoreForwardedHost` が見るヘッダ。`Headers` でも素のオブジェクトでもよい。 */
export interface ForwardedHeaders {
  get(name: string): string | null;
}

/**
 * `X-Forwarded-Host` / `X-Forwarded-Proto` があれば URL のホストを差し替える。
 *
 * ヘッダが無ければ入力をそのまま返す（ローカル開発やプロキシを通らない経路では
 * `req.url` が既に正しい）。
 */
export function restoreForwardedUrl(rawUrl: string, headers: ForwardedHeaders): string {
  const forwardedHost = headers.get("x-forwarded-host");
  if (!forwardedHost) return rawUrl;

  const forwardedProto = headers.get("x-forwarded-proto") ?? "https";
  const url = new URL(rawUrl);
  if (url.host === forwardedHost && url.protocol === `${forwardedProto}:`) {
    return rawUrl;
  }

  if (forwardedHost.includes(":")) {
    url.host = forwardedHost;
  } else {
    url.hostname = forwardedHost;
    url.port = "";
  }
  url.protocol = `${forwardedProto}:`;
  return url.toString();
}
