# アテマル（netkeirin 予想家 id=665）の逆解析 — 2026-08-25

記録は memory `keirin_atemaru_reverse_engineering_2026_08_25`。

```bash
python3 fetch_atemaru.py 20260801 20260824 0.7   # 生HTMLを ./atemaru/{list,detail} へ保存
python3 parse_atemaru.py                          # -> atemaru/parsed.jsonl
KEIRIN_DB_URL=... python3 join_db.py              # -> atemaru/joined.jsonl（wt_entries/wt_odds と結合）
python3 analyze.py 20260801 20260824              # 窓ごとの集計
python3 pooled.py                                 # 両窓プール + paired bootstrap
```

- **レース確定後の予想だけが無料で読める**（未確定は「この予想は未購入です」）＝事後分析専用
- 取得間隔は既定 0.7 秒。生HTMLは必ずアーカイブしてから解析する（`src/scraper/netkeirin.py` と同じ方針）
- 詳細ページに「予想の転載はお控えください」の表記がある。**内部分析限定**とし買い目を再配信しない
