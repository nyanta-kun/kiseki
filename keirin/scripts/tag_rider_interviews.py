#!/usr/bin/env python3
"""未分類の選手コメントを `claude -p` で分類して `rider_interviews` へ書く（2026-10-01）。

🔴 **日次の分類は `claude -p`（Claude Code のサブスクリプション枠）で行う**（ユーザー決定 2026-09-30）。
   過去分（2025-12〜2026-09・142,624件）は API の Batch で分類済みで、`load_rider_interviews.py` で入れる。
🔴 **`ANTHROPIC_API_KEY` を環境から外して呼ぶ。** 入っていると `claude -p` は API の従量課金になる。
⚠️ **claude にログイン済みの Mac で動かす**（VPS には claude が無い）。

    PYTHONPATH=. .venv/bin/python3 scripts/tag_rider_interviews.py            # 未分類を全部
    PYTHONPATH=. .venv/bin/python3 scripts/tag_rider_interviews.py --limit 200 # 試し

⚠️ 分類の尺度は過去分（API Batch）と少し違う（同じ160件で「調子が良い」55 ↔ 37件）。画面の表示用には
   十分だが、事前登録の判定には判定窓を API Batch で分類し直して使う
   （`docs/type_lab/prereg_rider_condition_2026_10_01.md` §3）。

実測（2026-09-30）: 40件で約80秒・入力約2.7万トークン（Claude Code の組み込み分）。思考を切ると約4倍速い。
呼び出しの固定費が大きいので1回あたり `--chunk` 件（既定 80）をまとめて渡す。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import get_connection  # noqa: E402
from src.rider_interviews import SYSTEM_PROMPT, TAG_MODEL, TAG_SCHEMA, apply_tags, valid_tag  # noqa: E402


def build_prompt(rows: list[dict]) -> str:
    """分類を頼む本文（通し番号 i と kind と本文）。"""
    body = "\n".join(json.dumps({"i": k, "kind": r["kind"], "text": r["body"]}, ensure_ascii=False)
                     for k, r in enumerate(rows))
    return f"次の {len(rows)} 件を分類してください。\n{body}"


def call_claude(prompt: str, timeout: int = 900) -> list[dict]:
    """`claude -p` で分類して記号の配列を返す。失敗は例外。"""
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    # 🔴 思考を切る（2026-10-01）。思考ありだと過去分（API Batch・思考なし）より「調子が良い」を
    #    倍近く付けた（同じ160件で 37 → 72件・一致率 62%）。切ると 55件・一致 69% で、
    #    API どうしの再分類のぶれ（45件・一致 74%）に近づく。速さも約4倍
    env["MAX_THINKING_TOKENS"] = "0"
    r = subprocess.run(
        ["claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence",
         "--output-format", "json", "--system-prompt", SYSTEM_PROMPT,
         "--json-schema", json.dumps(TAG_SCHEMA)],
        input=prompt, capture_output=True, text=True, env=env, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p rc={r.returncode}: {r.stderr[:300]}")
    out = json.loads(r.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude -p error: {str(out.get('result'))[:300]}")
    so = out.get("structured_output") or json.loads(out.get("result") or "{}")
    return so.get("x") or []


def main() -> None:
    """CLI エントリ。"""
    ap = argparse.ArgumentParser(description="選手コメントを claude -p で分類する")
    ap.add_argument("--chunk", type=int, default=80)
    ap.add_argument("--limit", type=int, default=0, help="この件数で止める（0=全部）")
    args = ap.parse_args()

    with get_connection() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, kind, body FROM rider_interviews WHERE tagged_at IS NULL "
            "ORDER BY race_date DESC, id")]
    if args.limit:
        rows = rows[:args.limit]
    done = bad = fail = 0
    t0 = time.time()
    for i in range(0, len(rows), args.chunk):
        part = rows[i:i + args.chunk]
        try:
            items = call_claude(build_prompt(part))
        except Exception as e:  # 1回の失敗で全体を止めない（残りは次の実行で拾う）
            fail += len(part)
            print(f"[fail] {e!r}"[:300])
            continue
        tagged = []
        for it in items:
            k = it.get("i")
            if isinstance(k, int) and 0 <= k < len(part) and valid_tag(it):
                tagged.append((part[k]["id"], it))
            else:
                bad += 1
        with get_connection() as c:
            done += apply_tags(c, tagged, model=f"{TAG_MODEL}(claude-p)")
    print(f"[tag-rider-interviews] 対象 {len(rows)} / 分類 {done} / 不正な出力 {bad} / 失敗 {fail} "
          f"/ {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
