import json,re,sys,time,urllib.request,urllib.parse,datetime as dt
from pathlib import Path
BASE="https://keirin.netkeiba.com"; YID=614
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
LIST=Path("gensen/list"); LIST.mkdir(parents=True,exist_ok=True)
def get(url,data=None):
    req=urllib.request.Request(url,data=data,headers={"User-Agent":UA})
    with urllib.request.urlopen(req,timeout=30) as r: return r.read().decode("utf-8","replace")
def fl(date):
    p=LIST/f"{date}.json"
    if p.exists(): return p.read_text(encoding="utf-8")
    body=urllib.parse.urlencode({"input":"UTF-8","output":"json","show_id":"goods_list_main","kaisai_date":date,"yosoka_id":YID,"jyo":"all"}).encode()
    s=get(f"{BASE}/yoso/api/api_get_goods_list_prof.html",body); p.write_text(s,encoding="utf-8"); time.sleep(0.4); return s
s,e=sys.argv[1],sys.argv[2]
d=dt.date(int(s[:4]),int(s[4:6]),int(s[6:])); ed=dt.date(int(e[:4]),int(e[4:6]),int(e[6:]))
tot=0
while d<=ed:
    ds=d.strftime("%Y%m%d")
    try:
        raw=fl(ds); ids=sorted(set(re.findall(r"umai_prof_goods_state_(b\d+_%d)"%YID,raw)))
    except Exception as ex:
        ids=[]; print(ds,"ERR",ex)
    tot+=len(ids); print(ds,len(ids),tot,flush=True)
    d+=dt.timedelta(days=1)
