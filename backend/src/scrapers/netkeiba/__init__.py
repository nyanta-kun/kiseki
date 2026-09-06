"""netkeiba スクレイパの共通層（sekito から移設・統合 Phase 2 後半）。

sekito 側は `bin/scrape/netkeiba`(3,235行) に本体があり、共通部品は
`lib/sekito/` に散っていた。kiseki へはこの 4 つに整理して持ってくる:

    decode.py        文字コード判定。**ホストごとに違う**ので決め打ち厳禁
    rate_limiter.py  時間帯別のレート制限。所要時間のほぼ全部がこの待ち
    ip_restriction.py IP 制限の検出と門番。**cron 起動の kiseki では自前で持つ必要がある**
    session.py       ログイン済み requests.Session

🔴 **IP 制限ゲートがこの移設の肝**:
    sekito では `scheduler.js` の `checkIpRestriction()` が、script_name に
    `netkeiba` / `odds` を含むジョブを実行前に弾いていた。**kiseki は cron 起動なので
    その門番が居ない。** ゲートを持たずに移すと、IP 制限中も叩き続けて制限を伸ばす。
    ここでは `require_not_restricted()` / `guard()` として明示的に持つ。

移設で意図的に変えたこと（いずれも根拠を各モジュールの docstring に書いた）:
    - `AdaptiveRateLimiter` を落とし、素の `RateLimiter` にした（適応部分は死んでいた）
    - ログイン確認ページの復号に `decode_page` を使う（移設元は EUC-JP 決め打ちだった）
    - IP 制限検出時に `scheduler_enabled` を触らない（再起動と組み合わさると
      復旧ジョブごと止まって自力で戻れなくなる）
"""
