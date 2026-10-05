"""読み取り専用の DB 接続ヘルパ（URL は出力しない）。"""
import os
import psycopg2


def connect():
    """KEIRIN_DB_URL へ読み取り専用で接続する。"""
    c = psycopg2.connect(os.environ["KEIRIN_DB_URL"])
    c.set_session(readonly=True, autocommit=True)
    return c


def q(sql, params=None):
    """SQL を実行して (列名, 行) を返す。"""
    with connect() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return [d[0] for d in cur.description], cur.fetchall()
