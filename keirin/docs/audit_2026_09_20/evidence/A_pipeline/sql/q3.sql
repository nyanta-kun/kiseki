SELECT rank_key, enabled, require_approval, axis_gate_enabled, updated_at
FROM keirin.netkeirin_settings ORDER BY enabled DESC, rank_key;
