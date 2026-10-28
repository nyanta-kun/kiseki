\o /tmp/claude-501/kesi/legs.csv
select t.race_key, t.plan_key, t.bet_type, t.budget, x->>'combo' combo, (x->>'stake')::int stake,
  to_char(s.published_at,'YYYY-MM-DD HH24:MI:SS') published_at, to_char(t.generated_at,'YYYY-MM-DD HH24:MI:SS') generated_at
from keirin.type_lab_picks t cross join jsonb_array_elements(t.legs) x
left join keirin.netkeirin_submissions s on s.race_key=t.race_key and s.rank_key=t.plan_key and s.published_at is not null and s.deleted_at is null
where t.mode='live' and t.n_entries=7 and t.race_date between '2026-06-08' and '2026-10-27';
\o /tmp/claude-501/kesi/snap_time.csv
select race_key, to_char(min(snapshot_at),'YYYY-MM-DD HH24:MI:SS') t0, to_char(max(snapshot_at),'YYYY-MM-DD HH24:MI:SS') t1
from keirin.wt_odds_snapshot where snapshot_type='morning' and bet_type='trifecta' and race_key between '20260608' and '20261028' group by 1;
\o /tmp/claude-501/kesi/payoffs.csv
select race_key, bet_type, combination, payoff from keirin.wt_payoffs where race_key between '20260608' and '20261028';
\o
