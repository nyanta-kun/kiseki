"""デプロイが keirin-webhook を再起動することを固定する（2026-08-11）。

## 背景（実害）

`keirin-webhook.service` は systemd の常駐プロセスで **Docker の外**にいる。
デプロイは `git reset --hard` + コンテナ入れ替えなので、ソースが新しくなっても
**このプロセスだけ古いコードのまま動き続ける**。

2026-08-11 に実際に起きた: 06:51 起動のプロセスが 14:12 更新のコードを読まず、
確認画面から承認しても何も起きなかった（承認・取消のルートが新規追加だったため）。

🔴 **`/health` では絶対に検知できない。** 旧コードでも生きているので死活監視は緑のまま。
   しかも `/submit-race` は旧コードにもあり「一部だけ動く」ので誤診しやすい。

だから **デプロイ手順に組み込む**しかない。ここが消えたら同じ障害が再発する。
"""
from __future__ import annotations

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy-bluegreen.sh"


def _script() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    """コメント行を除いた実行行だけを返す。

    ⚠️ コメントに書いてあるだけで「ある」と判定しないため
       （この種の検査を docstring の偽陽性で2度通してしまった前例がある）。
    """
    out = []
    for line in _script().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def test_デプロイスクリプトが存在する():
    assert DEPLOY.exists(), f"{DEPLOY} が見つかりません（配置が変わりました）"


def test_webhookを再起動する行がある():
    """🔴 これが消えると承認・取消が古いコードのまま動き、しかも気づけない。"""
    restart = [ln for ln in _code_lines()
               if re.search(r"systemctl\s+restart\s+keirin-webhook", ln)]
    assert restart, (
        "deploy-bluegreen.sh に keirin-webhook の再起動がありません。"
        "systemd 常駐なのでコンテナ入れ替えでは新しいコードを読みません"
    )


def test_再起動の失敗でデプロイ全体を落とさない():
    """Web 本体は既に切り替わっている。ここで落とすと成功したデプロイが失敗になる。
    代わりに警告を出して手動再起動を促すこと。"""
    code = "\n".join(_code_lines())
    idx = code.index("systemctl restart keirin-webhook")
    after = code[idx: idx + 600]
    assert "exit 1" not in after, (
        "webhook の再起動失敗でデプロイを exit 1 にしています。"
        "Web 本体は既に切り替わっているので、警告に留めてください"
    )
    assert "err " in after, "失敗時に警告を出していません（黙って失敗すると気づけない）"


def test_再起動は成功パスの最後に置く():
    """ヘルスチェックやマイグレーションより前に置くと、
    デプロイが途中で失敗したときに webhook だけ新しくなる。"""
    code = _script()
    assert code.index("systemctl restart keirin-webhook") > code.index("alembic upgrade head"), (
        "webhook の再起動が DB マイグレーションより前にあります"
    )
