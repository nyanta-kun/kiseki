#!/bin/bash
export NETKEIBA_INTERVAL=2.0
cd /Users/ysuzuki/GitHub/kiseki/keirin/scripts/exp_hot
# フェーズ1（profile.py 4本）の完了を待つ
while pgrep -f "profile.py (614|506|482|428) 20260820" > /dev/null; do sleep 20; done
python3 an_hipay_614_482_428_506.py > an_hipay.log 2>&1
python3 an_match_428_482.py > an_match.log 2>&1
echo PHASE2_DONE
