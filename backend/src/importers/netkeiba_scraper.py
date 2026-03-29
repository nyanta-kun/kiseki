"""netkeiba プレミアム会員スクレイピング

対象: db.netkeiba.com/race/{race_id}/
取得フィールド:
  - 備考（出遅れ・不利・後方一気等の短評テキスト）: 馬ごと
  - 注目馬レース後の短評: 上位入線馬のみ
  - 分析コメント: レース全体

ペース（S/M/H）は races.first_3f / last_3f_race から自力計算するため非スクレイピング。

IP制限対策:
  - リクエスト間に 3〜5秒のランダムウェイト
  - 同一(race_id, horse_id)の取得済みレコードはスキップ
  - 429/403 受信で即時停止
"""

import logging
import random
import re
import time

import httpx

logger = logging.getLogger(__name__)

LOGIN_URL = "https://regist.netkeiba.com/account/"
RACE_RESULT_URL = "https://db.netkeiba.com/race/{netkeiba_id}/"

_WAIT_MIN = 3.0
_WAIT_MAX = 5.0


def jv_to_netkeiba_id(jravan_race_id: str) -> str:
    """JV-Link 16文字 race_id → netkeiba 12文字 race_id。

    JV-Link形式: YYYY + MMDD + CC + KK + DD + RR (16文字)
    netkeiba形式: YYYY + CC + KK + DD + RR (12文字、日付MMDD部分を除去)
    """
    if len(jravan_race_id) != 16:
        raise ValueError(f"jravan_race_id は16文字である必要があります: {jravan_race_id!r}")
    return jravan_race_id[0:4] + jravan_race_id[8:16]


def create_session(user_id: str, password: str) -> httpx.Client:
    """netkeibaにログインしてセッションを返す。

    Raises:
        RuntimeError: ログイン失敗時
    """
    client = httpx.Client(
        follow_redirects=True,
        timeout=30.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )

    # ログインページ取得でCookieを初期化
    client.get(f"{LOGIN_URL}?pid=login")

    resp = client.post(
        LOGIN_URL,
        data={
            "pid": "login",
            "action": "auth",
            "login_id": user_id,
            "pswd": password,
            "return_url2": "",
            "mem_tp": "",
        },
    )

    # www.netkeiba.comにアクセスしてドメイン全体にCookieを確立
    client.get("https://www.netkeiba.com/")

    # ログイン確認（nkauth cookieの存在で判定）
    if "nkauth" not in client.cookies:
        client.close()
        raise RuntimeError("netkeiba ログイン失敗: nkauth cookie が取得できませんでした")

    logger.info("netkeiba ログイン成功")
    return client


def _decode(content: bytes) -> str:
    """EUC-JPデコード。"""
    return content.decode("euc-jp", errors="replace")


def _wait() -> None:
    """IP制限対策のランダムウェイト。"""
    time.sleep(random.uniform(_WAIT_MIN, _WAIT_MAX))


def scrape_race(client: httpx.Client, jravan_race_id: str) -> dict:
    """1レース分のデータをスクレイピングして返す。

    Args:
        client: ログイン済みhttpxセッション
        jravan_race_id: JV-Link 16文字 race_id

    Returns:
        {
          "race_analysis": str | None,          # 分析コメント（レース全体）
          "horses": [                            # 馬ごとのデータ
            {
              "horse_name": str,
              "remarks": str | None,             # 備考（出遅れ等）
              "notable_comment": str | None,     # 注目馬短評（上位馬のみ）
            }, ...
          ]
        }

    Raises:
        httpx.HTTPStatusError: 429/403等の場合
    """
    netkeiba_id = jv_to_netkeiba_id(jravan_race_id)
    url = RACE_RESULT_URL.format(netkeiba_id=netkeiba_id)
    logger.info("スクレイピング: %s (netkeiba: %s)", jravan_race_id, netkeiba_id)

    resp = client.get(url)

    if resp.status_code in (429, 403):
        raise httpx.HTTPStatusError(
            f"レート制限またはアクセス拒否: {resp.status_code}",
            request=resp.request,
            response=resp,
        )

    resp.raise_for_status()
    text = _decode(resp.content)

    return {
        "race_analysis": _parse_race_analysis(text),
        "horses": _parse_horse_remarks(text),
        "notable_comments": _parse_notable_comments(text),
    }


def _parse_race_analysis(text: str) -> str | None:
    """分析コメントを抽出する。

    <th>分析コメント<img ...></th><td>テキスト</td> の形式。
    th内にimgタグが含まれるため .*? でスキップする。
    """
    m = re.search(
        r'<th[^>]*>分析コメント.*?</th>\s*<td[^>]*>(.*?)</td>',
        text,
        re.DOTALL,
    )
    if not m:
        return None
    return re.sub(r'<[^>]+>', '', m.group(1)).strip() or None


def _parse_horse_remarks(text: str) -> list[dict]:
    """各馬の備考（出遅れ・不利等）を抽出する。

    備考セルの位置:
      <diary_snap_cut>
        <td class="txt_c " nowrap>  ← 調教タイムアイコン (ico_oikiri.gif)
        <td class="txt_c " nowrap>  ← 厩舎コメントアイコン (ico_comment.gif)
        <td nowrap="nowrap">        ← 備考（クラスなし、div.txt_cを含む）
      </diary_snap_cut>
    """
    results = []

    horse_link_re = re.compile(r'<a href="/horse/[^/"]+/"[^>]*title="([^"]+)"')

    # 備考を含む diary_snap_cut ブロックのパターン
    # ico_oikiri と ico_comment の後に続く <td nowrap> が備考列
    remarks_block_re = re.compile(
        r'ico_oikiri\.gif.*?ico_comment\.gif.*?'
        r'<td nowrap="nowrap">\s*(.*?)\s*<div class="txt_c">',
        re.DOTALL,
    )

    # メイン結果テーブルを取得
    table_m = re.search(
        r'<table[^>]*class="[^"]*nk_tb_common[^"]*"[^>]*>(.*?)</table>',
        text,
        re.DOTALL,
    )
    table_text = table_m.group(1) if table_m else text

    # 馬ごとのtr行を抽出
    for row in re.finditer(r'<tr[^>]*>(.*?)</tr>', table_text, re.DOTALL):
        row_html = row.group(1)

        horse_m = horse_link_re.search(row_html)
        if not horse_m:
            continue
        horse_name = horse_m.group(1)

        # 備考セルを抽出
        remarks_m = remarks_block_re.search(row_html)
        if remarks_m:
            raw = re.sub(r'<[^>]+>', '', remarks_m.group(1)).strip()
            remarks = raw if raw else None
        else:
            remarks = None

        results.append({
            "horse_name": horse_name,
            "remarks": remarks,
        })

    return results


def _parse_notable_comments(text: str) -> dict[str, str]:
    """注目馬レース後の短評を {馬名: コメント} で返す。

    上位入線馬のみ掲載されるため全馬分ではない。
    """
    result: dict[str, str] = {}

    table_m = re.search(
        r'<table[^>]*summary="注目馬 レース後の短評"[^>]*>(.*?)</table>',
        text,
        re.DOTALL,
    )
    if not table_m:
        return result

    table = table_m.group(1)

    # thが「N着:馬名」、次のtdがコメント
    entries = re.findall(
        r'<th>\d+着:([^<]+)</th>\s*</tr>\s*<tr>\s*<td>(.*?)</td>',
        table,
        re.DOTALL,
    )
    for horse_name, comment in entries:
        comment_clean = re.sub(r'<[^>]+>', '', comment).strip()
        if horse_name and comment_clean:
            result[horse_name.strip()] = comment_clean

    return result


def compute_pace(first_3f: float | None, last_3f: float | None) -> str | None:
    """JV-Dataのタイムからペース（S/M/H）を計算する。

    races.first_3f（前半3F秒）とlast_3f_race（後半3F秒）の差で判定。
    - 前半 - 後半 < -1.0 → S（スロー: 前半ゆっくり）
    - 前半 - 後半 > +1.0 → H（ハイ: 前半速い）
    - それ以外            → M（ミドル）

    スクレイピング不要。races テーブルから直接計算できる。
    """
    if first_3f is None or last_3f is None:
        return None
    diff = float(first_3f) - float(last_3f)
    if diff < -1.0:
        return "S"
    elif diff > 1.0:
        return "H"
    else:
        return "M"
