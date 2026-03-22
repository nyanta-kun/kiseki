"""実DBデータでスピード指数算出を検証するスクリプト。"""
import os
import sys
sys.path.insert(0, "/app/src" if os.path.exists("/app") else str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(__import__("pathlib").Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)

with Session(engine) as db:
    from src.indices.speed import SpeedIndexCalculator
    from src.db.models import Race, RaceEntry, RaceResult

    # 1. 基準タイムのサンプル確認
    print("=== 基準タイム算出サンプル確認 ===")
    rows = db.execute(text("""
        SELECT r.course, r.distance, r.surface, r.condition,
               COUNT(*) as cnt,
               ROUND(AVG(rr.finish_time)::numeric, 2) as avg_time,
               ROUND(STDDEV_POP(rr.finish_time)::numeric, 3) as std_time
        FROM keiba.race_results rr
        JOIN keiba.races r ON rr.race_id = r.id
        WHERE rr.finish_time IS NOT NULL AND rr.abnormality_code = 0
        GROUP BY r.course, r.distance, r.surface, r.condition
        ORDER BY cnt DESC
        LIMIT 10
    """)).fetchall()

    print(f"{'コース':>4} {'距離':>5} {'芝ダ':>4} {'馬場':>4} {'件数':>5} {'平均(秒)':>9} {'標準偏差':>8}")
    print("-" * 50)
    for r in rows:
        print(f"{r.course:>4} {r.distance:>5} {r.surface:>4} {str(r.condition or '-'):>4} {r.cnt:>5} {r.avg_time:>9} {r.std_time:>8}")

    # 2. 過去実績のある馬の2026/3/22出走レースで算出
    print("\n=== 阪神大賞典(G2) スピード指数算出 ===")
    # 中山R4 (course=06, race_number=4) を使ってみる - コスモゴレアドールが出走
    target_race = db.query(Race).filter(
        Race.date == "20260322",
        Race.course == "06",
        Race.race_number == 4
    ).first()

    if target_race:
        print(f"対象レース: {target_race.course} R{target_race.race_number} {target_race.race_name} {target_race.distance}m {target_race.surface}")
        calc = SpeedIndexCalculator(db)
        results = calc.calculate_batch(target_race.id)

        entries = db.query(RaceEntry).filter(RaceEntry.race_id == target_race.id).all()
        from src.db.models import Horse
        print(f"\n{'馬番':>4} {'馬名':>20} {'スピード指数':>10} {'過去実績':>6}")
        print("-" * 50)
        for entry in sorted(entries, key=lambda e: e.horse_number):
            horse = db.query(Horse).filter(Horse.id == entry.horse_id).first()
            idx = results.get(entry.horse_id, 50.0)
            past = db.query(RaceResult).filter(RaceResult.horse_id == entry.horse_id).count()
            marker = " ← 過去データあり" if past > 0 else " (データなし→デフォルト50)"
            print(f"{entry.horse_number:>4} {(horse.name if horse else '?'):>20} {idx:>10.1f}{marker}")

    # 3. 過去実績馬の詳細確認
    print("\n=== 過去実績馬の詳細（タガノバルコス・コスモゴレアドール） ===")
    results_detail = db.execute(text("""
        SELECT h.name, r.date, r.course, r.distance, r.surface, r.condition,
               rr.finish_position, rr.finish_time, re.weight_carried
        FROM keiba.race_results rr
        JOIN keiba.races r ON rr.race_id = r.id
        JOIN keiba.horses h ON rr.horse_id = h.id
        JOIN keiba.race_entries re ON re.race_id = rr.race_id AND re.horse_id = rr.horse_id
        WHERE h.name IN ('コスモゴレアドール', 'タガノバルコス')
        ORDER BY r.date DESC
    """)).fetchall()

    for r in results_detail:
        print(f"  {r.date} {r.course} {r.distance}m {r.surface}{r.condition} {r.finish_position}着 {r.finish_time}秒 {r.weight_carried}kg")

print("\n検証完了")
