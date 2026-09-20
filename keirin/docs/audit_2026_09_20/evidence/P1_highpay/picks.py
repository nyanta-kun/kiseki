"""本番関数で1レース1プランの買い目を組み、確定オッズで採点する。"""
from __future__ import annotations
import sys
sys.path.insert(0, "/private/tmp/claude-501/-Users-ysuzuki-GitHub-kiseki/5a78b9ec-b8ca-47de-8015-b5191bfb9e70/scratchpad/audit/P1_highpay")
import lib
from src import type_lab as TL
from src.stake_allocation import MIN_MEAN_PAYOUT, MIN_POINT_ODDS


def build_pick(r, plan, order_probs=None):
    """本番の build_with_gate_fallback を通して (legs, stakes, plan_used) を返す。"""
    if plan.bet_type == "trio":
        odds, prob = r["tpo"], r["tprob"]
    else:
        odds, prob = r["po"], r["probs"]
    got = TL.build_with_gate_fallback(r["shape"], plan, odds, prob, 7,
                                      order_probs=None)
    if not got:
        return None
    return got


def gate_ok(legs, stakes, odds):
    """入稿ゲート（平均想定払戻 > 2万円 ∧ 全点の予測オッズ >= 2.0）。"""
    mp = TL.mean_expected_payout(stakes, odds)
    if mp <= MIN_MEAN_PAYOUT:
        return False, "mean_payout"
    o = [float(odds[c]) for c in legs]
    if o and min(o) < MIN_POINT_ODDS:
        return False, "point_odds"
    return True, None


def score(r, legs, stakes, bet_type):
    """確定オッズで採点。戻り: dict(bet, payout, hit, n_legs, pred_mean, ...)"""
    bet = sum(stakes.values())
    if bet_type == "trio":
        win, pay = r["twin"], r["tpay"]          # tpay = 確定三連複オッズ
        odds = r["tpo"]
        hitleg = win if (win is not None and win in stakes) else None
        payout = (stakes[hitleg] / 100.0) * pay * 100 if hitleg is not None else 0.0
        # tpay は確定オッズ（倍）。払戻 = 賭け金 × オッズ
        payout = stakes[hitleg] * pay if hitleg is not None else 0.0
        fin = pay
    else:
        win, pay = r["win"], r["pay"]            # pay = 円/100円
        odds = r["po"]
        hitleg = win if (win is not None and win in stakes) else None
        payout = (stakes[hitleg] / 100.0) * pay if hitleg is not None else 0.0
        fin = pay / 100.0
    return dict(bet=bet, payout=payout, hit=hitleg is not None,
                n_legs=len(legs),
                pred_mean=TL.mean_expected_payout(stakes, odds),
                pred_min=TL.min_expected_payout(stakes, odds),
                final_odds_win=fin if hitleg is not None else None)
