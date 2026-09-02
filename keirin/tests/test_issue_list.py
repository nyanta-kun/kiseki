"""課題通知（Discord `review` ch）の整形と歯止めを固定する（2026-09-02 新設）。

ここで縛るのは4つ。**どれもプロンプトでは守れないのでコードで止める**もの。

  1. §1 の `[NG]` だけを拾う（他の節の文字列を巻き込まない）
  2. 節ごとの行数上限と「…他 N 件」への畳み込み
  3. 前夜と同一なら送らない（毎晩同じ列が流れると読まれなくなる）
  4. Discord の 2000 文字制限を超えない

加えて **通知の性格**（状態と次の行動だけ・単日の成績数字は載せない）を、
テンプレート側が数字を足していないことで固定する。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src.notify.issue_list import (  # noqa: E402
    DISCORD_LIMIT, EMPTY_DIGEST, Message, Section, build_anomaly_message,
    contains_daily_performance, digest, remember, render, should_send)

_spec = importlib.util.spec_from_file_location(
    "notify_issues", REPO / "scripts" / "notify_issues.py")
notify_issues = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(notify_issues)  # type: ignore[union-attr]


MD = """# 型ラボ 夜間レビュー  2026-08-30（日）

## §1 異常検知 — **単日で黒白がつく唯一の層**
  [OK] 入稿 57件（morning 57）— 直近7日の中央値 36件
  [OK] 1レース1商品（重複なし）
  ---- 見送り理由 closed=5, gate_mean_payout=6
  [OK] 並び・印の欠測なし
  [NG] 未採点の型ラボ行 50件（32レース） — intraday_results を確認
  [NG] 売った商品のうち採点できなかったもの 22件

## §2 当日成績（売った商品）— **単日では判断しない**
  [NG] これは §1 ではないので拾ってはいけない
  表示的中 17.1% / ROI 29.7%
"""


def test_1の_NGだけを拾う() -> None:
    ng, n_ok = notify_issues.parse_alerts(MD)
    assert len(ng) == 2, ng
    assert n_ok == 3
    assert all("§1 ではない" not in t for t in ng)
    # 接頭辞 `[NG] ` は落として本文だけを渡す（整形側で付け直す）。
    assert ng[0].startswith("未採点の型ラボ行")


def test_1が無い日でも落ちない() -> None:
    ng, n_ok = notify_issues.parse_alerts("# 見出しだけ\n\n本文\n")
    assert ng == [] and n_ok == 0


def test_上限を超えた分は畳まれる() -> None:
    msg = build_anomaly_message("2026-09-02", [f"異常{i}" for i in range(9)])
    body = render(msg)
    assert "・…他 4 件" in body
    assert body.count("・[異常]") == 5
    # 件数は畳んでも失われない（何件あるかが分からないのが一番困る）。
    assert "**今夜やること** (9)" in body


def test_承認待ちは畳まない() -> None:
    sec = Section("承認待ち", [f"・H{i:04d}" for i in range(8)], cap=None)
    body = render(Message(day="2026-09-02", sections=[sec]))
    assert "他" not in body
    assert body.count("・H") == 8


def test_2000文字を超えない() -> None:
    msg = build_anomaly_message(
        "2026-09-02", ["あ" * 900 for _ in range(5)], url="https://x/y.html")
    body = render(msg)
    assert len(body) <= DISCORD_LIMIT
    # 切り詰めたことが本文に残る（黙って切ると「無い」と読まれる）。
    assert "省略" in body


def test_指紋は日付とリンクと件数に依存しない() -> None:
    a = build_anomaly_message("2026-09-01", ["未採点 50件"], url="https://x/1", n_ok=3)
    b = build_anomaly_message("2026-09-02", ["未採点 50件"], url="https://x/2", n_ok=7)
    assert digest(a) == digest(b)
    c = build_anomaly_message("2026-09-02", ["未採点 51件"])
    assert digest(c) != digest(a)


def test_前夜と同一なら送らない(tmp_path: Path) -> None:
    state = tmp_path / ".notified.json"
    msg = build_anomaly_message("2026-09-01", ["未採点 50件"])
    fp = digest(msg)
    assert should_send(state, "anomaly", fp) is True
    remember(state, "anomaly", fp, "2026-09-01")
    assert should_send(state, "anomaly", fp) is False
    # 中身が変われば送る。
    fp2 = digest(build_anomaly_message("2026-09-02", ["未採点 51件"]))
    assert should_send(state, "anomaly", fp2) is True


def test_異常ゼロなら送らない(tmp_path: Path) -> None:
    assert should_send(tmp_path / "s.json", "anomaly", EMPTY_DIGEST) is False


def test_状態ファイルが壊れていても送れる(tmp_path: Path) -> None:
    # 🔴 差分抑止のための状態が壊れたときに**通知が止まる**のが最悪。
    state = tmp_path / ".notified.json"
    state.write_text("{壊れている", encoding="utf-8")
    assert should_send(state, "anomaly", "abc123") is True


def test_テンプレートは単日の成績数字を足さない() -> None:
    # 載せるのは状態と次の行動だけ。§2 の数字（表示的中・ROI・払戻）は
    # 「5〜95% の内側なら情報を持たない」と毎晩自分で証明しているもの。
    body = render(build_anomaly_message("2026-09-02", ["未採点 50件（32レース）"],
                                        url="https://x/y.html", n_ok=4))
    assert not contains_daily_performance(body), body


def test_成績語の検査そのもの() -> None:
    assert contains_daily_performance("ROI 29.7%")
    assert contains_daily_performance("表示的中 17.1%")
    assert not contains_daily_performance("未採点 50件（32レース）")


def test_reviewチャンネルが残っている() -> None:
    # 消すと送信先が無くなり、`send` が「未設定です」で静かに False を返す。
    from src.notify.discord import _WEBHOOK_ENV_KEYS
    assert _WEBHOOK_ENV_KEYS["review"] == "DISCORD_WEBHOOK_URL_REVIEW"
    # results（成績報告）とは別物であること。
    assert _WEBHOOK_ENV_KEYS["review"] != _WEBHOOK_ENV_KEYS["results"]


@pytest.mark.parametrize("kind", ["anomaly"])
def test_CLIが未生成の日で1を返す(kind: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(notify_issues, "NIGHTLY", tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["notify_issues.py", "--day", "1999-01-01", "--kind", kind])
    assert notify_issues.main() == 1
