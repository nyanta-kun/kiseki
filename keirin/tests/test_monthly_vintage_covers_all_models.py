"""月次の vintage 自動生成が、walk-forward 再構築が要求する**全種類**を作ること。

## なぜこの検査が要るか（2026-09-01 の障害）

`train_monthly_vintage_models.py` の学習対象は **eval / win / bad / top2 の4種固定**で、
`favbust`（7H1 が使う）が入っていなかった。favbust を作れるのは
`train_favbust_model.py` だけなのに、**それを呼ぶ cron / シェルが1つも無かった**
（2026-08-06 に手で m2404〜m2608 を一括生成したきりの完全な手動運用）。

その結果、最初の月替わりである **2026-09-01 に `lgbm_wt_favbust_m2609` が無く**、
`rebuild_7h1_walkforward_pg.py` が計算を開始せず毎朝 🚨 を出す状態になった。
実害は「9月の 7H1 の紙の成績が候補プール行のまま残る」ことと、
**本物の障害通知が埋もれること**。

🔴 **モデルの種類が増えたときに人が気づく必要がある形にしない。**
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENSURE = REPO / "scripts" / "ensure_monthly_vintage.sh"
FAVBUST = REPO / "scripts" / "train_favbust_model.py"
MONTHLY = REPO / "scripts" / "train_monthly_vintage_models.py"


def test_monthly_job_trains_favbust_too():
    """毎月1日のジョブが favbust も学習すること。"""
    sh = ENSURE.read_text(encoding="utf-8")
    assert "train_monthly_vintage_models.py" in sh, "eval/win/bad/top2 の学習が無い"
    assert "train_favbust_model.py" in sh, (
        "favbust の学習が月次ジョブに無い。"
        "train_monthly_vintage_models.py は favbust を作らないので、"
        "これが無いと毎月1日に 7H1 の再構築が止まる（2026-09-01 の障害）")


def test_monthly_favbust_call_is_safe():
    """月次の favbust 呼び出しに、上書き防止と鮮度確保の両方が付いていること。"""
    # ⚠️ コメント中にもスクリプト名が出るので、**実行行**だけを取り出す
    #    （最初の出現を拾うと自分のコメントを検査してしまう）。
    sh = ENSURE.read_text(encoding="utf-8")
    lines = sh.splitlines()
    idx = next((n for n, ln in enumerate(lines)
                if "train_favbust_model.py" in ln and not ln.lstrip().startswith("#")), None)
    assert idx is not None, "favbust の実行行が見つからない（コメントだけ？）"
    call = " ".join(lines[idx:idx + 3])
    assert "--only-missing" in call, (
        "--only-missing が無い。`--vintages` は全月を無条件に再学習し、"
        "凍結した過去 vintage を黙って上書きする")
    assert "--rebuild-cache" in call, (
        "--rebuild-cache が無い。学習セットはキャッシュされ、付けないと古いまま"
        "（2026-09-01 時点で max race_date が 2026-08-06 だった）")


def test_favbust_script_supports_only_missing():
    """`--only-missing` が実装されており、既存モデルへ触らないこと。"""
    src = FAVBUST.read_text(encoding="utf-8")
    assert '"--only-missing"' in src, "--only-missing が未実装"
    assert "only_missing" in src and ".pkl\").exists()" in src, \
        "既存モデルの存在チェックが無い（上書きしてしまう）"


def test_favbust_fit_refuses_when_trainset_is_stale():
    """🔴 学習セットが学習窓を覆っていなければ学習しないこと。

    付けないとキャッシュが古いまま「M月の前月末まで」という契約を
    **例外もログも無しに**破る（8/06 までのデータで 8/31 までのモデルを作る）。
    """
    src = FAVBUST.read_text(encoding="utf-8")
    assert "newest < upto" in src, "学習セットの鮮度チェックが無い"


def test_favbust_is_distributed_to_vps():
    """学習しても VPS へ届かなければ意味がない。"""
    sync = (REPO / "scripts" / "sync_models_to_vps.sh").read_text(encoding="utf-8")
    assert "favbust" in sync, "favbust が VPS への配布対象に入っていない"


def test_shortage_notice_names_the_right_script():
    """🔴 不足通知が **効かない対処**を案内していないこと。

    2026-09-01 の通知は `train_monthly_vintage_models.py --only-missing` を
    案内していたが、そのスクリプトは favbust を学習しないので**空回りする**。
    次に踏む人が確実に無駄足を踏む。
    """
    src = (REPO / "scripts" / "rebuild_7h1_walkforward_pg.py").read_text(encoding="utf-8")
    i = src.index("vintageモデル不足のため計算を開始せず中断")
    msg = src[i:i + 1200]
    assert "train_favbust_model.py" in msg, \
        "favbust のときの正しい対処（train_favbust_model.py）が案内されていない"


# ─────────────── `_fit` のガードを実際に動かす ───────────────

def _load_favbust_module():
    """本物のモデルディレクトリへ触らせずに import する。"""
    import importlib.util
    import sys

    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("favbust_train", FAVBUST)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["favbust_train"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_fit_skips_existing_model_when_only_missing(tmp_path, monkeypatch):
    """🔴 `--only-missing` は既にあるモデルへ触らない（凍結 vintage の保護）。"""
    mod = _load_favbust_module()
    monkeypatch.setattr(mod, "MODEL_DIR", tmp_path)
    (tmp_path / "lgbm_wt_favbust_m2608.pkl").write_bytes(b"frozen")

    rows = [{"race_date": "2026-08-31", "bust": 0}] * 10
    mod._fit(rows, "lgbm_wt_favbust_m2608", "2026-07-31", only_missing=True)

    assert (tmp_path / "lgbm_wt_favbust_m2608.pkl").read_bytes() == b"frozen", \
        "既存の vintage を上書きした"


def test_fit_refuses_when_trainset_does_not_cover_the_window(tmp_path, monkeypatch):
    """🔴 学習セットが窓を覆っていなければ学習しない（静かな契約違反の防止）。

    2026-09-01 の実データがまさにこれ: キャッシュは 2026-08-06 までしか無く、
    m2609（upto=2026-08-31）を学習すると 8/06 までのデータでモデルができる。
    """
    mod = _load_favbust_module()
    monkeypatch.setattr(mod, "MODEL_DIR", tmp_path)

    rows = [{"race_date": "2026-08-06", "bust": 0}] * 10
    mod._fit(rows, "lgbm_wt_favbust_m2609", "2026-08-31")

    assert not (tmp_path / "lgbm_wt_favbust_m2609.pkl").exists(), \
        "学習セットが古いのに学習した"


def test_fit_still_trains_when_the_window_is_covered(tmp_path, monkeypatch):
    """ガードが常時 skip になっていないこと（件数不足で落ちるところまで進む）。"""
    mod = _load_favbust_module()
    monkeypatch.setattr(mod, "MODEL_DIR", tmp_path)

    rows = [{"race_date": "2026-08-31", "bust": 0}] * 10   # 3,000件未満なので学習はしない
    mod._fit(rows, "lgbm_wt_favbust_m2609", "2026-08-31")
    # ここまで例外なく到達すれば、ガードで早期 return していない＝件数チェックまで進んだ
    assert not (tmp_path / "lgbm_wt_favbust_m2609.pkl").exists()
