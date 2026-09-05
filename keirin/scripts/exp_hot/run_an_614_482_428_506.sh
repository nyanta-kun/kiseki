#!/bin/bash
export NETKEIBA_INTERVAL=2.0
cd /Users/ysuzuki/GitHub/kiseki/keirin/scripts/exp_hot
python3 profile.py 614 20260820 20260905 --stride 2  > an_614.log 2>&1
python3 profile.py 506 20260820 20260905 --stride 3  > an_506.log 2>&1
python3 profile.py 482 20260820 20260905 --stride 7  > an_482.log 2>&1
python3 profile.py 428 20260820 20260905 --stride 10 > an_428.log 2>&1
echo DONE_PROFILES
