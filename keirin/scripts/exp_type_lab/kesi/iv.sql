select kind, race_key, cup_id, race_date, player_id, condition, trouble, fatigue, equipment, self_eval, trend, tactic, confidence
from keirin.rider_interviews where condition is not null or trouble is not null;
