"""H01 目視: 2025 の1レースを本番関数どおり組み、legs/オッズ/配分/ゲートを印字。同 plan_key の live 行(2026)と構造を突き合わせる。"""
import pickle, sys
from h01_step1 import *   # noqa

z, idx = prep()
want = sys.argv[1:] or ["A_hit", "B_hit", "C_hit", "E_hit", "F_hit"]
shown = set()
for i in idx[3000:]:
    x = make_ctx(i, z)
    if x is None:
        continue
    main, _ = R.race_rows(x, {}) if False else (None, None)
    ARM["v"] = "cur"; R.build = build_dispatch
    main, _ = R.race_rows(x, {})
    if main is None or main["plan"] not in want or main["plan"] in shown or not x.lb_events:
        continue
    shown.add(main["plan"])
    plan = TL.PLANS[main["plan"]]
    print("=" * 70)
    print(x.key, x.date, "type", x.shape.type_label, "axis_sum %.3f" % x.shape.axis_sum, "rtype", x.rtype,
          "→ plan", main["plan"], "gate軸信頼:", R._G.passes_axis_gate(main["plan"], float(x.shape.axis_sum), 7))
    print("LB events:", x.lb_events, " 的中:", x.win_tf, "確定", x.pay_tf)
    for arm in ("cur", "lb"):
        ARM["v"] = arm
        st, odds, used, mean = build_dispatch(x, plan)
        print(f"-- {arm}: {len(st)}点 投資{sum(st.values())} 平均想定払戻{mean:,.0f} gate_ok={S.gate_ok(st, odds, mean)} 最低予測オッズ{min(odds[c] for c in st):.1f}")
        for c, s in sorted(st.items(), key=lambda kv: odds[kv[0]]):
            print(f"   {c}  賭け{s:>5} 予測{odds[c]:>7.1f} 払戻{s*odds[c]:>8,.0f}  P_cur={x.pr_tf[c]:.4f} P_lb={x.pr_tf_lb[c]:.4f}")
        print("   採点(inv,pay)=", S.settle(x, st, used.bet_type == "trio"))
    if len(shown) >= len(want):
        break
