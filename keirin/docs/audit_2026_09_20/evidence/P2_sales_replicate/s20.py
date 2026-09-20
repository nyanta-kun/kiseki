from common import *
g=pd.read_pickle("daily.pkl").copy(); R,REF=0.30,0.3
tot=g["paid"].sum(); tl=g[g.index>="20260829"]
print(f"49日 有償pt {tot:,.0f} → 売上 {tot*R:,.0f}円 → 手取り {tot*R*(1-REF):,.0f}円 ({tot*R*(1-REF)/49:,.0f}円/日)")
print(f"型ラボ期21日: {tl['paid'].mean():,.0f}pt/日 → 手取り {tl['paid'].mean()*R*(1-REF):,.0f}円/日 → 月換算 {tl['paid'].mean()*R*(1-REF)*30:,.0f}円")
print(f"直近7日(09/12-18): {g.loc['20260912':'20260918','paid'].mean():,.0f}pt/日 → 手取り {g.loc['20260912':'20260918','paid'].mean()*R*(1-REF):,.0f}円/日")
for nm,pt in [("高額枠 1本(5枠期平均1,493pt)",1493),("高額枠 1本(限界・10枠期6本目以降774pt)",774),
              ("自信あり 1件の上乗せ(+1,804pt)",1804),("通常商品 1本(326pt)",326)]:
    print(f"  {nm:<40} → 手取り {pt*R*(1-REF):,.0f}円")
print(f"\n高額枠を5→10本に増やした実測: 高額枠売上 6,967→6,798pt/日（{(6798-6967)*R*(1-REF):+,.0f}円/日）")
