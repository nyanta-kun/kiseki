"""学習環境(Mac)と推論環境(VPS)の版ずれを機械的に検出する（2026-08-11 新設）。

## なぜ要るのか

2026-08-11 に **Mac 4.6.0 / VPS 4.3.0** のずれが見つかった。`requirements.txt` は
4.3.0 を宣言していたので、**逸脱していたのは学習側の Mac**。結果として
「モデル成果物を、作った版より古い版で読む」という壊れやすい向きになっていた。

実害は出ていなかった（全モデルで予測がビット単位一致することを実測で確認）が、
気づいたのは新モデルが吐いた `Ignoring unrecognized parameter` 警告が**たまたま**
目に入ったからで、**それが無ければ誰も気づかないまま**だった。

同型の「宣言と実体がずれても誰も落ちない」問題はこのリポジトリで繰り返している
（[[keirin_cutover_done_2026_08_10]] の絶対パス4件・モデル配布リストの追加漏れ）。
そこで宣言（requirements.txt）と実体（インストール済み）の一致をテストで固定する。

⚠️ このテストが落ちたら**版を上げ下げする前に両機を確認すること**。
   片側だけ動かすと、静かに「新しい版で作って古い版で読む」向きへ戻る。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQ_FILES = ("requirements.txt", "requirements_vps_slim.txt")

# 版ずれが「壊れやすい向き」を生むパッケージ＝モデル成果物を書いて読むもの。
# ここに挙げたものだけ厳密一致を要求する（他は環境差を許容する）。
ARTIFACT_PACKAGES = ("lightgbm",)


def _pinned(req: str, package: str) -> str | None:
    """`package==X.Y.Z` の X.Y.Z を返す。コメント行は無視する。"""
    pat = re.compile(rf"^{re.escape(package)}==([0-9][0-9A-Za-z.\-]*)\s*$", re.IGNORECASE)
    for line in (ROOT / req).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = pat.match(line)
        if m:
            return m.group(1)
    return None


@pytest.mark.parametrize("package", ARTIFACT_PACKAGES)
def test_requirements_files_agree(package):
    """2つの requirements が同じ版を宣言していること（片方だけ直す事故の防止）。"""
    pins = {req: _pinned(req, package) for req in REQ_FILES}
    present = {k: v for k, v in pins.items() if v is not None}
    assert present, f"{package} がどの requirements にも固定されていません: {pins}"
    assert len(set(present.values())) == 1, (
        f"{package} の宣言が食い違っています: {present}。"
        "片方だけ更新すると VPS と Mac がずれます"
    )


@pytest.mark.parametrize("package", ARTIFACT_PACKAGES)
def test_installed_version_matches_pin(package):
    """実際にインストールされている版が宣言と一致すること。

    落ちたときは「どちらが正しいか」を先に決めること。モデルを**書く**側
    （学習環境）の版を、**読む**側（本番）が下回ってはいけない。
    """
    pin = next((p for req in REQ_FILES if (p := _pinned(req, package))), None)
    assert pin, f"{package} のピンが読み取れません"
    mod = pytest.importorskip(package)
    installed = getattr(mod, "__version__", None)
    assert installed == pin, (
        f"{package} の版が宣言と違います: インストール済み {installed} / 宣言 {pin}。"
        "学習(Mac)と推論(VPS)で揃っているかを確認してから直すこと"
        "（片側だけ動かすと『新しい版で作って古い版で読む』向きへ戻ります）"
    )
