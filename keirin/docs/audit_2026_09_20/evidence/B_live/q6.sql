SELECT substring(race_date,1,6) ym, count(*) n,
       sum(n_predictions) npred, sum(n_predictions_staked) nstk,
       sum(stake_amount) stake, sum(payout_amount) payout,
       round(100.0*sum(payout_amount)/NULLIF(sum(stake_amount),0),2) roi,
       sum(n_hits_incl_garami) hin, sum(n_hits_excl_garami) hex
FROM keirin.netkeirin_sales_race GROUP BY 1 ORDER BY 1;
