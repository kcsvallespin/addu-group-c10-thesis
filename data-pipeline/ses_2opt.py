"""Offline SES: re-order each day of saved final itineraries (2-opt, and best hours-respecting order).
No API calls. Needs scipy.
"""
import json,glob,math,itertools,statistics as st,datetime as dt
from scipy import stats
DAYS=['mon','tue','wed','thu','fri','sat','sun']
def tm(t): h,m=t.split(':'); return int(h)*60+int(m)
def hav(a,b):
    R=6371.0088;p1,p2=math.radians(a[0]),math.radians(b[0]);dl=math.radians(b[1]-a[1]);dp=p2-p1
    h=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2;return 2*R*math.asin(math.sqrt(h))
def ok_hours(hours,date,s,e):
    if not hours: return True
    rng=hours.get(DAYS[dt.date.fromisoformat(date).weekday()])
    if rng is None: return True
    if rng=='closed': return False
    S,E=tm(s),tm(e)
    for part in rng.split(','):
        o,c=(tm(x) for x in part.strip().split('-'))
        if c<=o: c+=1440
        if o<=S and E<=c: return True
    return False
def plen(order,xy): return sum(hav(xy[a],xy[b]) for a,b in zip(order,order[1:]))
def two_opt(order,xy):
    best=list(order);imp=True
    while imp:
        imp=False
        for i in range(0,len(best)-1):
            for j in range(i+1,len(best)):
                new=best[:i]+best[i:j+1][::-1]+best[j+1:]
                if plen(new,xy)<plen(best,xy)-1e-9: best=new;imp=True
    return best
def tight(order,slots,xy):
    n=0
    for k,(a,b) in enumerate(zip(order,order[1:])):
        gap=tm(slots[k+1][0])-tm(slots[k][1]); need=hav(xy[a],xy[b])/25*60
        if gap<need-10: n+=1
    return n
def run(folder):
    out=[]
    for f in sorted(glob.glob(folder+'/**/S*.json',recursive=True)):
        d=json.load(open(f));sid=d['scenario']['id']
        cand={c['venue_id']:c for c in d['candidates']}
        for day in d['itinerary']['itinerary']:
            sl=sorted(day['slots'],key=lambda s:tm(s['time_start']))
            if len(sl)<2: continue
            ids=[s['venue_id'] for s in sl]; slots=[(s['time_start'],s['time_end']) for s in sl]
            xy={i:(float(cand[i]['latitude']),float(cand[i]['longitude'])) for i in ids}
            hrs={i:cand[i].get('operating_hours') for i in ids}
            if isinstance(next(iter(hrs.values())),str): pass
            D0=plen(ids,xy)
            t2=two_opt(ids,xy);D2=plen(t2,xy)
            def valid(order): return all(ok_hours(hrs[v],day['date'],*slots[k]) for k,v in enumerate(order))
            orig_valid=valid(ids)
            v2=valid(t2)
            # best feasible permutation (exact, <=6 stops), positional time slots
            bestp=ids;bd=D0
            for perm in itertools.permutations(ids):
                dd=plen(perm,xy)
                if dd<bd-1e-9 and valid(perm): bestp=list(perm);bd=dd
            out.append(dict(sid=sid,date=day['date'],n=len(ids),D0=D0,D2=D2,D2_valid=v2,orig_valid=orig_valid,Dfeas=bd,
                            tight0=tight(ids,slots,xy),tight2=tight(t2,slots,xy),tightf=tight(bestp,slots,xy)))
    return out
if __name__=='__main__':
    import sys
    # usage: python ses_2opt.py itineraries_mimo_v2 itineraries_deepseek_v2
    for fo in sys.argv[1:]:
        nm=fo
        r=run(fo); print('==',nm,'days',len(r),'orig hours-valid',sum(x['orig_valid'] for x in r))
        D0=sum(x['D0'] for x in r);D2=sum(x['D2'] for x in r);Df=sum(x['Dfeas'] for x in r)
        print(f'total km  draft {D0:.0f}  2opt {D2:.0f} (SES {100*(D0-D2)/D0:.1f}%)  2opt-valid-only-days {sum(x["D2_valid"] for x in r)}/{len(r)}  feasible-best {Df:.0f} (SES {100*(D0-Df)/D0:.1f}%)')
        # per scenario SES
        per={}
        for x in r: per.setdefault(x['sid'],[0,0,0]); per[x['sid']][0]+=x['D0'];per[x['sid']][1]+=x['D2'];per[x['sid']][2]+=x['Dfeas']
        ses=[100*(a-b)/a for a,b,c in per.values() if a>0];sesf=[100*(a-c)/a for a,b,c in per.values() if a>0]
        print('per-scenario SES(2opt) mean',round(st.mean(ses),1),'median',round(st.median(ses),1),'n improved',sum(1 for v in ses if v>0.05),'of',len(ses),'| feasible mean',round(st.mean(sesf),1),'improved',sum(1 for v in sesf if v>0.05))
        a=[v[0] for v in per.values()];b=[v[1] for v in per.values()]
        print('wilcoxon draft vs 2opt dist',stats.wilcoxon(a,b,method='auto'))
        print('days improved',sum(1 for x in r if x['D2']<x['D0']-1e-6),'days changed feasible',sum(1 for x in r if x['Dfeas']<x['D0']-1e-6))
        print('tight legs: draft',sum(x['tight0'] for x in r),'2opt',sum(x['tight2'] for x in r),'feasible',sum(x['tightf'] for x in r))
        print('max single day saving km',round(max(x['D0']-x['D2'] for x in r),1))
