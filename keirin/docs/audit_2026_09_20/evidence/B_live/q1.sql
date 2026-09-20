SELECT substring(ns.race_key,1,6) ym, ns.status,
  count(*) n,
  count(*) FILTER (WHERE ns.settled_at IS NULL) unsettled,
  count(*) FILTER (WHERE ns.settled_at IS NULL AND ns.bet_detail IS NOT NULL) unsettled_bd
FROM keirin.netkeirin_submissions ns
WHERE ns.deleted_at IS NULL AND ns.status IN ('published','submitted')
GROUP BY 1,2 ORDER BY 1,2;
