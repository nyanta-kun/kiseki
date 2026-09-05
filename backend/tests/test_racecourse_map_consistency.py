"""競馬場コード対応表の二重管理がずれないよう機械的に固定する。

なぜ必要か（2026-09-05）:
    競馬場のコード対応は同じ内容を 2 箇所で持たざるを得ない。

      - `src/utils/racecourse.py` … Python から使う（指数計算・API）
      - `alembic/versions/202609052115_shared_add_racecourse_map.py` の SEED
        … `keiba.racecourse_map` に入り、SQL の JOIN から使われる

    マイグレーションにアプリ側の定数を import させれば重複は消せるが、
    マイグレーションは「その時点のスナップショット」であるべきで、後で
    モジュールが動いただけで過去のマイグレーションが壊れる。
    そこで重複は受け入れ、代わりに**ずれたら落ちる**ようにする。

    ずれたときに何が起きるか: 地方の指数クエリは
    `JOIN keiba.racecourse_map rc ON r.course = rc.netkeiba_id` の形なので、
    対応が 1 行欠けるとその競馬場のレースが**丸ごと JOIN から外れ、
    外部指数が NULL になるだけでエラーは出ない**。
    このリポジトリが繰り返し踏んでいる「静かに欠損する」型そのもの。

守る不変条件:
    1. モジュールとマイグレーション SEED の内容が完全一致する。
    2. モジュール内部の整合（コード重複なし・中央/地方の判定と jra_code の有無が一致）。

🔴 競馬場を足す・変えるときは、モジュールと**新しいマイグレーション**の両方を
   直したうえで、下の EXPECTED_SEED_MODULE を新しい方へ向け直すこと。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.utils.racecourse import (
    BY_CODE,
    JRA_TO_SEKITO,
    RACECOURSES,
    SEKITO_TO_JRA,
    is_jra,
)

_BACKEND = Path(__file__).resolve().parents[1]
# 対応表を最後に変更したマイグレーション。競馬場を足したら向け先を更新する。
EXPECTED_SEED_MODULE = (
    _BACKEND / "alembic" / "versions" / "202609052115_shared_add_racecourse_map.py"
)


def _load_seed() -> tuple[tuple, ...]:
    """マイグレーションを import して SEED を取り出す。

    alembic の `op` は import 時点では使わないので、モジュール単体で読める。
    """
    spec = importlib.util.spec_from_file_location(
        "_racecourse_seed_migration", EXPECTED_SEED_MODULE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SEED


def test_module_matches_migration_seed() -> None:
    """Python 側とマイグレーション側の対応表が完全一致すること。"""
    seed = _load_seed()
    module_rows = tuple(tuple(rc) for rc in RACECOURSES)

    assert len(seed) == len(module_rows), (
        f"件数が違う: マイグレーション {len(seed)} 行 / モジュール {len(module_rows)} 行。"
        " 競馬場を足したなら両方を直すこと"
    )

    seed_by_code = {r[0]: r for r in seed}
    module_by_code = {r[0]: r for r in module_rows}

    assert seed_by_code.keys() == module_by_code.keys(), (
        "コードの集合が違う: "
        f"マイグレーションのみ={sorted(seed_by_code.keys() - module_by_code.keys())} / "
        f"モジュールのみ={sorted(module_by_code.keys() - seed_by_code.keys())}"
    )

    for code in sorted(seed_by_code):
        assert seed_by_code[code] == module_by_code[code], (
            f"{code} の内容が違う:\n"
            f"  マイグレーション: {seed_by_code[code]}\n"
            f"  モジュール      : {module_by_code[code]}"
        )


def test_codes_are_unique() -> None:
    codes = [rc.code for rc in RACECOURSES]
    assert len(codes) == len(set(codes)), "code が重複している"
    assert len(BY_CODE) == len(RACECOURSES)


@pytest.mark.parametrize("rc", RACECOURSES, ids=lambda rc: rc.code)
def test_jra_code_presence_matches_prefix(rc) -> None:
    """中央(J始まり)だけが jra_code を持ち、地方は持たない。

    JV-Link の 2 桁課コードは中央にしか無いので、地方に値が入っていたら
    どこかで中央用のコードを流用している。
    """
    if is_jra(rc.code):
        assert rc.jra_code is not None, f"{rc.code} は中央なのに jra_code が無い"
        assert rc.jra_code == rc.netkeiba_id, (
            f"{rc.code}: 中央は netkeiba_id と JRA 課コードが一致するはず"
            f"（netkeiba={rc.netkeiba_id} / jra={rc.jra_code}）"
        )
    else:
        assert rc.jra_code is None, f"{rc.code} は地方なのに jra_code が入っている"


def test_jra_maps_round_trip() -> None:
    """JRA 2桁 ↔ sekito 4文字 が双方向で一対一であること。"""
    assert len(JRA_TO_SEKITO) == 10, "中央は10場のはず"
    assert len(SEKITO_TO_JRA) == len(JRA_TO_SEKITO), "逆引きで潰れている組がある"
    for jra, sekito in JRA_TO_SEKITO.items():
        assert SEKITO_TO_JRA[sekito] == jra
        assert is_jra(sekito)


def test_replaced_hardcoded_maps_are_equivalent() -> None:
    """置き換え前に 4 箇所へ重複していた中央10場の対応と一致すること。

    移行時に取りこぼしが無かったことを固定する。値は置き換え前の
    indices/anagusa.py / indices/paddock.py の SEKITO_COURSE_MAP と同じもの。
    """
    legacy_sekito_to_jra = {
        "JSPK": "01", "JHKD": "02", "JFKS": "03", "JNGT": "04", "JTOK": "05",
        "JNKY": "06", "JCKO": "07", "JKYO": "08", "JHSN": "09", "JKKR": "10",
    }
    assert SEKITO_TO_JRA == legacy_sekito_to_jra
    assert JRA_TO_SEKITO == {v: k for k, v in legacy_sekito_to_jra.items()}
