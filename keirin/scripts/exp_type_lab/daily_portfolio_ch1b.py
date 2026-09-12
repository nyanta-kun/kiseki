import sys; sys.path.insert(0,'scripts/exp_type_lab')

import numpy as np
import daily_portfolio_ch1 as _c1
fit_p, dispersion, boot_null = _c1.fit_p, _c1.dispersion, _c1.boot_null
import daily_portfolio as D
for nm, rows in (("探索", D.EX), ("確認", D.CF)):
    for label, sub in (("上限なし（軸ゲートのみ・件/日 33〜34）", [r for r in rows if r["gate"]]),
                       ("母集団ぜんぶ（件/日 36〜37）", rows)):
        p = fit_p(sub, lambda r: r["plan"])
        phi, O, E, V = dispersion(sub, p)
        null = boot_null(sub, p, 800)
        print(f"{nm} {label:38s} n={len(sub):6,} 日={len(set(r['date'] for r in sub)):4d} "
              f"φ={phi:.3f} 帰無95%={np.percentile(null,95):.3f} p={(null>=phi).mean():.3f}")
