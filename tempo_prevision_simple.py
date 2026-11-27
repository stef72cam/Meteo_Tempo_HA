#!/usr/bin/env python3
"""TEMPO V2 — official RTE PP thresholds, probabilistic forecast, HA-compatible JSON."""
import base64
import csv
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CLIENT_ID = os.getenv('RTE_CLIENT_ID', 'REMPLIR ICI')
CLIENT_SECRET = os.getenv('RTE_CLIENT_SECRET', 'REMPLIR ICI')
ENABLE_METEO = True
ENABLE_ENR_METEO = True
ENABLE_DEBUG = False
ENABLE_WHITE_BELLMAN = True  # Experimental, affects only white/blue.
WHITE_BELLMAN_MAX = 0.30
WHITE_BELLMAN_START = 0.85
PARIS = ZoneInfo('Europe/Paris')
UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parent
BASE = 'https://digital.iservices.rte-france.com/'
TOKEN_URL = BASE + 'token/oauth/'
API = BASE + 'open_api/'
SIGNAL_URL = API + 'signal/v2/signals'
SHORT_URL = API + 'consumption/v1/short_term'
WEEK_URL = API + 'consumption/v1/weekly_forecasts'
ANNUAL_URL = API + 'consumption/v1/annual_forecasts'
GEN_URL = API + 'generation_forecast/v3/forecasts'
TEMPO_URL = API + 'tempo_like_supply_contract/v1/tempo_like_calendars'
RED_TOTAL, WHITE_TOTAL = 22, 43
COLORS = ('bleu', 'blanc', 'rouge')
TUNNEL = {11:(0,3),12:(6,6),1:(13,19),2:(20,22),3:(22,22)}
# Provisional uncertainty, NOT RTE coefficients nor calibrated probabilities.
SIGMA = {1:.020,2:.035,3:.050,4:.075,5:.090,6:.105}
GEN_TYPES = ('WIND_ONSHORE','WIND_OFFSHORE','SOLAR')
STEP = dt.timedelta(minutes=15)

# ENR weather model: indicative regional weights, NOT a registry of individual parks.
# SDES 2026-Q3 (published 2026-11-26): France 24.6 GW onshore, 2.3 GW offshore,
# 35.4 GW PV including islands/overseas. Mainland-only modeling approximates
# 24.5 / 2.3 / 34.3 GW; regional weights below are rounded proxy weights.
# https://www.statistiques.developpement-durable.gouv.fr/tableau-de-bord-eolien-troisieme-trimestre-2026
# https://www.statistiques.developpement-durable.gouv.fr/tableau-de-bord-solaire-photovoltaique-troisieme-trimestre-2026
# name, lat, lon, relative onshore weight, relative PV weight
ENR_LAND = [
 ('Somme',49.90,2.55,3.1,.5),('Pas-de-Calais',50.35,2.35,2.2,.4),('Aisne',49.75,3.55,1.7,.4),
 ('Marne',48.95,4.35,2.6,1.2),('Aube',48.40,4.25,1.4,.8),('Lorraine',48.85,6.25,1.2,.9),
 ('Beauce',48.05,1.65,1.8,2.2),('Bretagne',48.15,-2.85,1.5,1.2),('Normandie',49.05,.65,1.1,.6),
 ('Vendee',46.65,-1.25,1.4,1.6),('Poitou',46.35,.05,1.25,2.4),('Landes',44.10,-.65,.8,4.6),
 ('Toulousain',43.65,1.65,.5,2.6),('Aude',43.13,2.65,1.35,2.8),('Bourgogne',47.40,4.70,1.3,1.8),
 ('Auvergne',45.80,3.25,.45,1.7),('Rhone',45.10,4.85,.25,2.6),('Provence',43.75,5.15,.1,2.4),
 ('Alpes du Sud',44.25,6.10,0,.8),('Ile-de-France',48.60,2.75,.15,.55),
]
# Offshore park clusters, approximate relative weights; no assumed future capacity.
ENR_SEA = [('Saint-Nazaire',47.15,-2.55,480),('Saint-Brieuc',48.85,-2.60,496),
 ('Fecamp',49.82,.22,497),('Yeu-Noirmoutier',46.88,-2.52,410),
 ('Dieppe-Le-Treport',50.10,1.05,357),('Mediterranee',43.0,3.2,60)]
ENR_CAPACITY = {'WIND_ONSHORE':24500.,'WIND_OFFSHORE':2300.,'SOLAR':34300.}


def valid_weather(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) and x>=0


def wind_factor(v, offshore=False):
    # Generic fleet power curve, not a manufacturer-specific turbine model.
    if not valid_weather(v): return None
    cutin,rated,cutout=(3.,12.,25.) if offshore else (3.,12.,25.)
    if v<cutin or v>=cutout: return 0.
    return min(1.,(v**3-cutin**3)/(rated**3-cutin**3))*(.90 if offshore else .86)


def weather_hourly(payload, points, sea=False):
    """Convert Open-Meteo UTC hours to interval-start MW by component.
    Wind is instantaneous; average endpoint power. Radiation is preceding-hour
    mean; take t+1 for interval [t,t+1). Reject incomplete spatial coverage.
    """
    if isinstance(payload,dict): payload=[payload]
    if not isinstance(payload,list) or len(payload)!=len(points): raise ValueError('Nombre de points ENR inattendu')
    sums=defaultdict(lambda:defaultdict(float)); coverage=defaultdict(lambda:defaultdict(float))
    kinds=('WIND_OFFSHORE',) if sea else ('WIND_ONSHORE','SOLAR')
    totals={k:sum(p[3 if k!='SOLAR' else 4] for p in points) for k in kinds}
    for obj,point in zip(payload,points):
        if not isinstance(obj,dict) or obj.get('error'): continue
        h=obj.get('hourly',{}); units=obj.get('hourly_units',{})
        if units.get('wind_speed_100m') not in ('m/s',None): raise ValueError('Unite vent ENR inattendue')
        if not sea and units.get('shortwave_radiation') not in ('W/m²','W/m2',None): raise ValueError('Unite rayonnement ENR inattendue')
        stamps=h.get('time',[]); wind=h.get('wind_speed_100m',[]); rad=h.get('shortwave_radiation',[])
        for i in range(len(stamps)-1):
            try:
                a=dt.datetime.fromisoformat(stamps[i]).replace(tzinfo=UTC)
                end=dt.datetime.fromisoformat(stamps[i+1]).replace(tzinfo=UTC)
                if end-a!=dt.timedelta(hours=1): continue
            except (ValueError,TypeError): continue
            for k in kinds:
                w=point[3 if k!='SOLAR' else 4]
                if not w: continue
                if k=='SOLAR':
                    if i+1>=len(rad) or not valid_weather(rad[i+1]): continue
                    # Fleet-average GHI proxy incl. orientation, losses and self-consumption.
                    f=min(1.,.80*rad[i+1]/1000.)
                else:
                    if i+1>=len(wind): continue
                    f0,f1=wind_factor(wind[i],sea),wind_factor(wind[i+1],sea)
                    if f0 is None or f1 is None: continue
                    f=(f0+f1)/2
                sums[k][a]+=w*f; coverage[k][a]+=w
    return {k:{a:ENR_CAPACITY[k]*v/totals[k] for a,v in sums[k].items()
               if math.isclose(coverage[k][a],totals[k],rel_tol=1e-8)} for k in kinds}


def get_enr_weather(today):
    # UTC avoids ambiguous DST hours; 9 UTC days cover J+7 and its 06h endpoint.
    # Separate land/sea to ensure the selected weather grid matches the site.
    def fetch(points,sea):
        return api_json('https://api.open-meteo.com/v1/forecast',params={
            'latitude':','.join(str(p[1]) for p in points),'longitude':','.join(str(p[2]) for p in points),
            'hourly':'wind_speed_100m' if sea else 'wind_speed_100m,shortwave_radiation',
            'wind_speed_unit':'ms','timezone':'GMT','forecast_days':9,'past_days':1,
            'cell_selection':'sea' if sea else 'land'})
    result={}; warnings=[]
    for points,sea in ((ENR_LAND,False),(ENR_SEA,True)):
        try: result.update(weather_hourly(fetch(points,sea),points,sea))
        except Exception as exc: warnings.append(f'ENR meteo {"mer" if sea else "terre"}: {type(exc).__name__}: {exc}')
    return {'series':result,'warnings':warnings}




def parse_ts(s):
    x = dt.datetime.fromisoformat(s.replace('Z','+00:00'))
    if x.tzinfo is None:
        raise ValueError('Timestamp sans fuseau horaire')
    return x.astimezone(UTC)


def midnight(d):
    return dt.datetime.combine(d, dt.time(), PARIS)


def params_dates(start, end):
    return {'start_date': midnight(start).isoformat(), 'end_date': midnight(end).isoformat()}


def api_json(url, token=None, params=None, data=None, headers=None):
    h = {'Accept':'application/json'}
    if token: h['Authorization'] = 'Bearer ' + token
    h.update(headers or {})
    if params: url += '?' + urlencode(params)
    with urlopen(Request(url, data=data, headers=h), timeout=12) as response:
        return json.load(response)


def get_token():
    if CLIENT_ID == 'REMPLIR ICI' or CLIENT_SECRET == 'REMPLIR ICI':
        raise ValueError('Renseigner CLIENT_ID et CLIENT_SECRET ou les variables RTE_CLIENT_ID/SECRET')
    auth = base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()
    return api_json(TOKEN_URL, data=urlencode({'grant_type':'client_credentials'}).encode(),
                    headers={'Authorization':'Basic '+auth,'Content-Type':'application/x-www-form-urlencoded'})['access_token']


def blocks(payload, key):
    x = payload.get(key, [])
    if isinstance(x, dict): return [x]
    return x if isinstance(x,list) else []


def values(block):
    return blocks(block,'values')


def season_start(d):
    return dt.date(d.year if d.month >= 9 else d.year-1, 9, 1)


def pp_start(d):
    return dt.date(d.year if d.month >= 11 else d.year-1,11,1)


def date_range(a,b):
    for i in range((b-a).days): yield a+dt.timedelta(days=i)


def season_code(d):
    y = pp_start(d).year
    return f'{y%100:02d}{(y+1)%100:02d}'


def easter(y):
    a=y%19; b,c=divmod(y,100); d,e=divmod(b,4); f=(b+8)//25; g=(b-f+1)//3
    h=(19*a+b-d-g+15)%30; i,k=divmod(c,4); l=(32+2*e+2*i-h-k)%7
    m=(a+11*h+22*l)//451; n,p=divmod(h+l-7*m+114,31)
    return dt.date(y,n,p+1)


def eligible(d):
    if d.month not in TUNNEL or d.weekday()>=5: return False
    if (d.month,d.day) in {(11,1),(11,11),(12,24),(12,25),(12,31),(1,1)}: return False
    return d != easter(d.year)+dt.timedelta(days=1)


class Thresholds:
    def __init__(self, root=ROOT): self.root=Path(root); self.cache={}; self.errors={}
    def load(self,d,lag):
        code=season_code(d); key=(code,lag)
        if key in self.cache: return self.cache[key]
        table={}; start=pp_start(d); end=dt.date(start.year+1,4,1)
        try:
            path=self.root/f'tableauseuils_pp_{code}_j-{lag}.csv'
            with path.open(encoding='utf-8-sig',newline='') as f:
                for row in csv.DictReader(f,delimiter=';'):
                    day=dt.datetime.strptime(row['date'],'%d/%m/%Y').date(); used=int(row['stockEpuise'])
                    t=float(row['seuilBellman']); reason=row['reason']
                    if not start<=day<end or not 0<=used<=22 or (day,used) in table or math.isnan(t) or t<0:
                        raise ValueError('ligne de matrice invalide ou dupliquée')
                    if (reason.startswith('BLOCKED_') and t!=math.inf or reason.startswith('FORCED_') and t!=0 or
                        reason=='BELLMAN_CALCULATED_THRESHOLD' and not 0<t<math.inf or
                        not reason.startswith(('BLOCKED_','FORCED_')) and reason!='BELLMAN_CALCULATED_THRESHOLD'):
                        raise ValueError('seuil/motif incohérent')
                    table[day,used]=(t,reason)
            if len(table)!=(end-start).days*23: raise ValueError('matrice incomplète')
        except (OSError,ValueError,KeyError) as exc:
            self.errors[f'{code}_j{lag}']=str(exc); table={}
        self.cache[key]=table; return table
    def get(self,d,used,lag=1):
        if not eligible(d): return math.inf,'CALENDAR_BLOCK'
        if used>=TUNNEL[d.month][1]: return math.inf,'MONTHLY_CAP'
        return self.load(d,lag).get((d,used),(None,'MATRIX_MISSING'))


def parse_signals(payload):
    out={}
    for block in blocks(payload,'signals'):
        if block.get('type')!='PP': continue
        for row in values(block):
            v=row.get('value')
            if type(v) is not bool: continue
            day=parse_ts(row['start_date']).astimezone(PARIS).date()
            updated=parse_ts(row['updated_date'])
            if day not in out or updated>out[day][1]: out[day]=(v,updated)
    return out


def parse_tempo(payload):
    out={}
    for block in blocks(payload,'tempo_like_calendars'):
        for row in values(block):
            v=str(row.get('value','')).lower()
            v={'blue':'bleu','white':'blanc','red':'rouge'}.get(v,v)
            if v in COLORS:
                day=parse_ts(row['start_date']).astimezone(PARIS).date()
                out[day]=v
    return out


def quarter_series(payload,key,now,kind=None):
    """Deduplicate forecasts by update; expand intervals to 15-minute UTC slots."""
    out={}
    for block in blocks(payload,key):
        typ=block.get('type')
        if key=='forecasts' and typ not in ('D-1','D-2','D-3','ID','CURRENT'): continue
        if key=='short_term' and typ not in (None,'D-1','D-2','D-3','ID','CURRENT'): continue
        if kind and block.get('production_type')!=kind: continue
        for row in values(block):
            try:
                a,b=parse_ts(row['start_date']),parse_ts(row['end_date'])
                v=float(row['value']); u=parse_ts(row.get('updated_date') or block.get('updated_date')) if (row.get('updated_date') or block.get('updated_date')) else dt.datetime.min.replace(tzinfo=UTC)
                if not math.isfinite(v) or v<0 or u>now or not dt.timedelta(0)<b-a<=dt.timedelta(hours=1): continue
                if a.minute%15 or a.second or b.minute%15 or b.second: continue
            except (KeyError,ValueError,TypeError): continue
            rank=(u,{'D-1':4,'D-2':3,'D-3':2,'CURRENT':5,'ID':6}.get(typ,0))
            while a<b:
                if a not in out or rank>out[a][0]: out[a]=(rank,v)
                a+=STEP
    return {a:v for a,(_,v) in out.items()}


def hourly(series,day):
    """Complete hours only, including 23/25-hour DST days."""
    a=midnight(day).astimezone(UTC); end=midnight(day+dt.timedelta(days=1)).astimezone(UTC); result={}
    while a<end:
        q=[series.get(a+i*STEP) for i in range(4)]
        if all(x is not None for x in q): result[a]=sum(q)/4
        a+=dt.timedelta(hours=1)
    return result


def daily_metrics(today,short,week,gen,now,enr_weather=None):
    s=quarter_series(short,'short_term',now); w=quarter_series(week,'weekly_forecasts',now)
    g={k:quarter_series(gen,'forecasts',now,k) for k in GEN_TYPES}
    # Legacy API WIND is accepted only when the split wind series is absent.
    if not g['WIND_ONSHORE'] and not g['WIND_OFFSHORE']:
        wind=quarter_series(gen,'forecasts',now,'WIND')
        if wind: g={'WIND':wind,'SOLAR':g['SOLAR']}
    last_profiles=[]; out={}; net_hours={}
    weather_series=(enr_weather or {}).get('series',{})
    # Legacy unsplit WIND is not compatible with per-component weather merging.
    if set(g)!=set(GEN_TYPES): weather_series={}
    # RTE national wind/solar for tomorrow may not be published in the morning.
    # Seed from the latest complete national profiles already in the response,
    # including today. CPC / cogeneration must never be used as national ENR.
    for day in date_range(today-dt.timedelta(days=2),today+dt.timedelta(days=8)):
        start=midnight(day).astimezone(UTC); end=midnight(day+dt.timedelta(days=1)).astimezone(UTC)
        expected=int((end-start).total_seconds()/3600)
        hours=[start+dt.timedelta(hours=i) for i in range(expected)]
        ch=hourly({**w,**s},day); gh={k:hourly(v,day) for k,v in g.items()}
        common=set.intersection(*(set(v) for v in gh.values())) if gh else set()
        direct={t:sum(v[t] for v in gh.values()) for t in common}
        source='RTE' if len(direct)==expected else 'missing'
        profile=direct.copy(); profile_dates=[]; weather_hours=0; persistence_hours=0
        component_sources={k: {'RTE':len(gh[k]),'meteo':0} for k in gh}
        if source=='RTE':
            # Average repeated local hours on the autumn clock-change day.
            local=defaultdict(list)
            for t,v in direct.items(): local[t.astimezone(PARIS).hour].append(v)
            last_profiles.append((day,{h:sum(v)/len(v) for h,v in local.items()}))
        if day<=today: continue
        if source!='RTE':
            # Build a complete national hour, preferring official RTE per component.
            for t in hours:
                if t in profile: continue
                vals={k:gh[k].get(t,weather_series.get(k,{}).get(t)) for k in gh}
                if all(valid_weather(v) for v in vals.values()):
                    profile[t]=sum(vals.values()); weather_hours+=1
                    for k in gh:
                        if t not in gh[k]: component_sources[k]['meteo']+=1
            # Never recycle an estimated day as if it were an official profile.
            recent=[(d,p) for d,p in last_profiles if (day-d).days<=8][-3:]
            by_hour=defaultdict(list)
            for d,p in recent:
                for h,v in p.items(): by_hour[h].append(v)
            for t in hours:
                h=t.astimezone(PARIS).hour
                if t not in profile and by_hour[h]:
                    profile[t]=sum(by_hour[h])/len(by_hour[h]); persistence_hours+=1
            if len(profile)==expected:
                source='estime_meteo_ENR' if weather_hours and not persistence_hours else 'estime_mixte_ENR' if weather_hours else 'estime_profil_horaire_ENR'
                if persistence_hours: profile_dates=[d.isoformat() for d,p in recent]
        complete=len(ch)==expected and len(profile)==expected and set(ch)==set(profile)
        net={t:ch[t]-profile[t] for t in ch if t in profile}
        net_hours.update(net)
        if not complete: net={}
        out[day]={'conso':sum(ch.values())/len(ch) if len(ch)==expected else None,
                  'gen':sum(profile.values())/len(profile) if len(profile)==expected else None,
                  'net':sum(net.values())/len(net) if net else None,'peak':max(net.values()) if net else None,
                  'gen_source':source,'gen_profile_dates':profile_dates,'gen_rte_hours':len(direct),'gen_weather_hours':weather_hours,'gen_persistence_hours':persistence_hours,'gen_component_sources':component_sources,'hours':len(ch),'matched_hours':len(net),'expected_hours':expected}
    # White days use the tariff window 06:00–06:00, not the PP calendar day.
    for day,m in out.items():
        a=dt.datetime.combine(day,dt.time(6),PARIS).astimezone(UTC)
        end=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(6),PARIS).astimezone(UTC)
        hours=int((end-a).total_seconds()/3600)
        vals=[net_hours.get(a+dt.timedelta(hours=i)) for i in range(hours)]
        m['net_tempo']=sum(vals)/hours if all(v is not None for v in vals) else None
        m['tempo_hours']=sum(v is not None for v in vals)
        m['tempo_expected_hours']=hours
    return {d:m for d,m in out.items() if d<=today+dt.timedelta(days=6)}


def quantile(vals,p):
    s=sorted(vals); i=p*(len(s)-1); a=int(i); return s[a]+(s[min(a+1,len(s)-1)]-s[a])*(i-a)


def annual_daily(payloads):
    out={}
    for payload in payloads:
        for block in blocks(payload,'annual_forecasts'):
            for row in values(block):
                try:
                    day=parse_ts(row['start_date']).astimezone(PARIS).date()
                    v=row.get('average_load_monday_to_sunday') or row.get('average_load_saturday_to_friday')
                    if isinstance(v,dict): v=v.get('value')
                    if v is None: continue
                    v=float(v)
                    if not math.isfinite(v) or v<=0: continue
                    for i in range(7): out[day+dt.timedelta(days=i)]=v
                except (KeyError,TypeError,ValueError): continue
    return out


def white_z(d,net,annual):
    if net is None: return None,None
    history=[v for day,v in annual.items() if 0<(d-day).days<=365]
    if len(history)>=30:
        a,b=quantile(history,.4),quantile(history,.8)
        if b-a>1: return (net-a)/(b-a),'annual_forecast'
    return (net-46050)/2160,'fallback_std'


def sigmoid(x):
    return 1/(1+math.exp(-max(-60,min(60,x))))


def white_threshold(d,red,white):
    j=(d-season_start(d)).days+1
    return 4.00-.015*j-.026*(red+white)-.30


def white_conditional(d,z,red,white,blue,offset,delta=None):
    if d.weekday()==6 or white<=0: return 0.
    if blue<=0: return 1.
    # White apurement: all remaining non-Sundays must be white if the
    # white stock equals the number of eligible slots (unless a red takes one).
    end=dt.date(season_start(d).year+1,9,1)
    eligible_white=sum(x.weekday()!=6 for x in date_range(d,end))
    if white>=eligible_white: return 1.
    if z is None: return None
    p=sigmoid((z-white_threshold(d,red,white))/.45)
    lam=min(.5,(offset-1)*.09)
    p=(1-lam)*p+lam*.5
    if delta is not None:
        strength=0 if abs(delta)<.5 else .03 if abs(delta)<1.5 else .06 if abs(delta)<3 else .09
        p+=strength if delta<0 else -strength
    return min(1,max(0,p))


def white_bellman_bonus(d,z,red,white,blue,offset,peak,threshold,base):
    """Conditional boost, not an RTE rule. Never alters PP/red probability."""
    if (not ENABLE_WHITE_BELLMAN or base is None or not 0<base<1 or
        z is None or peak is None or threshold is None or
        not 0<threshold<math.inf or d.weekday()==6 or white<=0 or blue<=0):
        return 0.
    # Proximity saturates above threshold in the non-red branch; a finite
    # threshold is mandatory. Z gates the bonus to avoid a narrow peak alone.
    approach=max(0.,min(1.,(peak/threshold-WHITE_BELLMAN_START)/(1-WHITE_BELLMAN_START)))
    gate=max(0.,min(1.,(z-white_threshold(d,red,white)+.75)/.75))
    reliability=1/(1+.15*(offset-1))
    return (1-base)*WHITE_BELLMAN_MAX*approach*gate*reliability


def red_probability(peak,threshold,offset,gen_source):
    if threshold==math.inf: return 0.
    if threshold==0: return 1.
    if threshold is None or peak is None: return None
    sigma=max(1000.,abs(peak)*SIGMA[offset])
    if gen_source!='RTE': sigma=math.hypot(sigma,4000.)
    # Continuous proxy for P(future decision forecast > threshold).
    return .5*math.erfc((threshold-peak)/(sigma*math.sqrt(2)))


def number(v,n=1): return round(v,n) if v is not None and math.isfinite(v) else None


def threshold_json(v): return 'Infinity' if v==math.inf else v


def known_pp(signals,day): return signals.get(day,(None,None))[0]


def late_pp(day,record):
    if not record or not record[0] or day>=dt.date(2027,11,1): return False
    return record[1].astimezone(PARIS)>=dt.datetime.combine(day-dt.timedelta(days=1),dt.time(10,30),PARIS)


def confidence(probs,offset,source,gen_source,uncertain):
    if probs is None: return None,'Indisponible','Données insuffisantes pour prévoir les couleurs.'
    if source=='override_RTE': return 5,'Très forte','Couleur Tempo publiée.'
    p=max(probs); score=5 if p>=.9 else 4 if p>=.75 else 3 if p>=.55 else 2 if p>=.4 else 1
    if source=='PP_OFFICIAL' and probs[2]==1 and not uncertain: score=5
    elif source=='PP_MATRIX_CONSTRAINT' and p==1 and not uncertain: score=5
    else:
        score=max(1,score-(offset>=4)-(gen_source!='RTE')-bool(uncertain))
    return score,('Très faible','Faible','Moyenne','Forte','Très forte')[score-1], 'Probabilités indicatives, non calibrées.'


def forecast(today,stocks,metrics,signals,tempo,thresholds,annual=None,weather=None,signal_ok=True,errors=None):
    annual=annual or {}; weather=weather or {}; errors=list(errors or [])
    red,white,blue=stocks; pp0=22-red
    sy=season_start(today); expected_days=(dt.date(sy.year+1,9,1)-today).days-1
    if sum(stocks)!=expected_days: errors.append(f'Somme des stocks Tempo ({sum(stocks)}) différente des jours après aujourd’hui ({expected_days}) : vérifier si le capteur inclut aujourd’hui ou demain.')
    start=pp_start(today+dt.timedelta(days=1)); past=[d for d in date_range(start,today+dt.timedelta(days=1)) if eligible(d)] if today.month in TUNNEL else []
    if today.month not in TUNNEL:
        pp0=0; pp_stock_source='outside_PP_season'
    elif signal_ok and all(d in signals for d in past):
        pp0=sum(known_pp(signals,d) is True for d in past); pp_stock_source='PP_history'
        if pp0!=22-red: errors.append('Stock PP différent du stock Tempo : suivi séparé (vérifier date de référence des capteurs).')
    else: pp_stock_source='Tempo_proxy'; errors.append('Historique PP incomplet : stock PP estimé à partir du stock Tempo.')
    if not 0<=pp0<=22: raise ValueError('Stock PP incohérent')
    states={(pp0,red,white,blue):1.}; result={}; unknown_prior=False
    displayed=[red,white,blue]; prev_start=season_start(today)
    for offset in range(1,7):
        d=today+dt.timedelta(days=offset); m=metrics.get(d,{}); peak=m.get('peak'); net=m.get('net'); net_tempo=m.get('net_tempo',net); white_net=net_tempo if net_tempo is not None else net; gen_source=m.get('gen_source','missing')
        if season_start(d)!=prev_start:
            n=(dt.date(d.year+1,9,1)-dt.date(d.year,9,1)).days
            states={(0,22,43,n-65):1.}; displayed=[22,43,n-65]; unknown_prior=False; prev_start=season_start(d)
        if (d.month,d.day)==(11,1):
            reset=defaultdict(float)
            for (_,r,w,b),p in states.items(): reset[(0,r,w,b)]+=p
            states=dict(reset)
        z,z_source=white_z(d,white_net,annual); tnat,tnorm=weather.get(d,(None,None)); delta=tnat-tnorm if tnat is not None and tnorm is not None else None
        official=tempo.get(d); pp=known_pp(signals,d); late=late_pp(d,signals.get(d)); notes=[]
        if gen_source in ('estime_meteo_ENR','estime_mixte_ENR'): notes.append('ENR estimees par modele meteo regional non calibre ; incertitude majoree.')
        if gen_source in ('estime_profil_horaire_ENR','estime_mixte_ENR'): notes.append('ENR estimées à partir de profils nationaux RTE antérieurs ; incertitude majorée.')
        if net_tempo is None and net is not None: notes.append('Blanc : moyenne 00h–24h utilisée faute de fenêtre 06h–06h complète.')
        if pp is True and not eligible(d): notes.append('Signal PP en dehors du calendrier attendu : vérifier la source.')
        if late and official is None: notes.append('PP publié/mis à jour après 10h30 J-1 : correspondance Tempo non certaine.')
        totals=[0.,0.,0.]; nxt=defaultdict(float); reasons=Counter(); diag=[]; missing=False; pp_missing=False; red_missing=False; pp_total=0.; p_early=0.; early_known=True; white_bonus_mass=0.; nonred_mass=0.
        for (used,r,w,b),weight in states.items():
            t,reason=thresholds.get(d,used); t2,reason2=thresholds.get(d,used,2)
            pr=red_probability(peak,t,offset,gen_source); pe=red_probability(peak,t2,offset,gen_source)
            # Already announced future PP days reserve places in the current monthly cap.
            reserved=sum(1 for x,rec in signals.items() if x>d and x.year==d.year and x.month==d.month and rec[0])
            if pp is not True and d.month in TUNNEL and used+reserved>=TUNNEL[d.month][1]: pr=0.; reason='RESERVED_PP_CAP'
            if pp is not None: pr=float(pp); reason='PP_OFFICIAL'
            elif official=='rouge': pr=1.; reason='TEMPO_RED_PP_PROXY'
            elif official in ('bleu','blanc'):
                pr=0.; reason='TEMPO_NON_RED_PP_PROXY'
            if pr is None: missing=True; pp_missing=True; red_missing=True; pr=.5 # propagation only; today's probabilities withheld
            if pe is None: early_known=False
            else: p_early+=weight*pe
            pp_total+=weight*pr; reasons[reason]+=weight
            diag.append({'pp_used':used,'weight':round(weight,6),'threshold_j1_mw':threshold_json(t),'threshold_j2_mw':threshold_json(t2),'reason':reason,'crosses_j1':(peak>t if peak is not None and t is not None else None),'crosses_j2':(peak>t2 if peak is not None and t2 is not None else None)})
            for is_pp,pp_weight in ((False,1-pr),(True,pr)):
                if not pp_weight: continue
                if official in COLORS:
                    probs=[float(official==c) for c in COLORS]
                else:
                    # Exceptional late PP cannot be promoted to a confirmed Tempo colour.
                    rr=float(is_pp and r>0 and eligible(d) and not late)
                    if late and is_pp and r>0: missing=True; red_missing=True; rr=.5
                    if rr==1: probs=[0.,0.,1.]
                    else:
                        pw=white_conditional(d,z,r,w,b,offset,delta)
                        if pw is None: missing=True; pw=.5
                        else:
                            bonus=white_bellman_bonus(d,z,r,w,b,offset,peak,math.inf if reason=='RESERVED_PP_CAP' else t,pw)
                            white_bonus_mass+=weight*pp_weight*(1-rr)*bonus
                            pw+=bonus
                        nonred_mass+=weight*pp_weight*(1-rr)
                        probs=[(1-rr)*(1-pw),(1-rr)*pw,rr]
                for i,p in enumerate(probs):
                    mass=weight*pp_weight*p
                    if not mass: continue
                    totals[i]+=mass; nr,nw,nb=r,w,b
                    if i==0: nb=max(0,b-1)
                    if i==1: nw=max(0,w-1)
                    if i==2: nr=max(0,r-1)
                    nxt[(min(22,used+int(is_pp and d.month in TUNNEL)),nr,nw,nb)]+=mass
        s=sum(totals); totals=[p/s for p in totals]; states={k:v/s for k,v in nxt.items() if v>0}
        source='override_RTE' if official else 'PP_OFFICIAL' if pp is not None and not late else 'PP_MATRIX_CONSTRAINT' if all(x['threshold_j1_mw'] in (0,'Infinity') for x in diag) and totals[2] in (0.,1.) else 'modele_interne'
        usable=not missing or official is not None or (pp is True and not late and totals[2]>.999999) or d.weekday()==6
        if missing: unknown_prior=True
        if not usable: notes.append('Probabilités masquées : données manquantes ; propagation interne indicative.')
        probs=totals if usable else None; color=COLORS[max(range(3),key=lambda i:totals[i])] if usable else 'inconnue'
        # Legacy remaining fields: most likely daily path, never negative. Separate expected stocks below.
        if color in COLORS:
            idx={'rouge':0,'blanc':1,'bleu':2}[color]; displayed[idx]=max(0,displayed[idx]-1)
        score,label,comment=confidence(probs,offset,source,gen_source,unknown_prior or pp_stock_source=='Tempo_proxy')
        # Keep sum of displayed probabilities exactly 1.000.
        rounded=None
        if probs is not None:
            ints=[int(x*1000) for x in probs]; order=sorted(range(3),key=lambda i:probs[i]*1000-ints[i],reverse=True)
            for i in order[:1000-sum(ints)]: ints[i]+=1
            rounded=[i/1000 for i in ints]
        expected=[sum(k[i]*v for k,v in states.items()) for i in (1,2,3)]
        result[f'J+{offset}']={'date':d.isoformat(),'couleur':color,'modele':source,'c_mw':number(m.get('conso')),'gen_mw':number(m.get('gen')),'gen_source':gen_source,'c_net_mw':number(net),'c_net_tempo_mw':number(net_tempo),'white_bellman_bonus':number(white_bonus_mass,4) if usable else None,'white_bellman_bonus_conditional':number(white_bonus_mass/nonred_mass,4) if usable and nonred_mass>0 else None,'z':number(z,3),'z_raw':number(z,3),'z_source':z_source,'T_nat':tnat,'T_norm':tnorm,'delta_T':number(delta,2),'meteo_bias':(0.0 if abs(delta)<.5 else .03 if abs(delta)<1.5 else .06 if abs(delta)<3 else .09) if delta is not None else None,'red_remaining':displayed[0],'white_remaining':displayed[1],'blue_remaining':displayed[2],'p_bleu':rounded[0] if rounded else None,'p_blanc':rounded[1] if rounded else None,'p_rouge':rounded[2] if rounded else number(totals[2],3) if not red_missing else None,'confidence_score':score,'confidence_label':label,'confidence_comment':comment,'red_deadline_pressure':None,'pp_status':'PP' if pp is True else 'NON_PP' if pp is False else 'PENDING' if signal_ok else 'UNAVAILABLE','pp_source':pp_stock_source,'pp_probability':number(pp_total,4) if not pp_missing or pp is not None else None,'pp_peak_mw':number(peak),'pp_thresholds':diag,'pp_early_probability_indicative':number(p_early,4) if early_known and offset==2 else None,'pp_reason':max(reasons,key=reasons.get),'pp_stock_expected_after':number(sum(k[0]*v for k,v in states.items()),3),'expected_remaining':dict(zip(('rouge','blanc','bleu'),[number(v,3) for v in expected])),'stock_uncertain':unknown_prior,'data_quality':{'gen_profile_dates':m.get('gen_profile_dates',[]),'gen_rte_hours':m.get('gen_rte_hours'),'gen_weather_hours':m.get('gen_weather_hours'),'gen_persistence_hours':m.get('gen_persistence_hours'),'gen_component_sources':m.get('gen_component_sources',{}),'hours':m.get('hours',0),'matched_hours':m.get('matched_hours',0),'expected_hours':m.get('expected_hours',24),'tempo_hours':m.get('tempo_hours'),'tempo_expected_hours':m.get('tempo_expected_hours'),'white_net_source':'06h-06h' if net_tempo is not None else 'calendar_day_fallback' if net is not None else 'missing','probabilities_calibrated':False,'notes':notes}}
        if ENABLE_DEBUG: result[f'J+{offset}']['z_debug']={'annual_days_count':len(annual),'states':len(states)}
    return result,errors

# National temperature weights and selected calendar normals carried over from V1.
STATIONS=[(50.136,1.834,1),(47.5896,7.5299,2),(44.8283,-.7156,4),(50.6589,1.6246,1),(47.0581,2.3703,4.2),(45.617,6.768,2.75),(48.4479,-4.4185,4.2),(49.1733,-.45,2.5),(45.7867,3.1692,2.75),(47.2689,5.0883,1),(43.3847,6.3872,1.2),(50.5619,3.0894,3),(45.8614,1.1794,3.2),(45.7256,5.0811,5.5),(43.4372,5.215,2.4),(43.5762,3.963,1.6),(48.6921,6.2303,3),(47.1532,-1.6107,4.2),(46.999,3.113,1.5),(43.6654,7.215,3.6),(43.8564,4.405,2.4),(44.1405,4.8667,1.2),(48.8218,2.3376,11.25),(42.7404,2.8707,1.6),(48.0695,-1.7348,4.2),(44.0583,5.9917,1.2),(48.5383,7.6282,1),(43.1786,-.0064,4),(43.6306,1.3638,1.6),(47.4322,.7276,4.2),(48.7742,1.9936,11.25),(48.3239,4.0179,1.5)]
NORMS={11: [11.14, 10.08, 10.92, 10.26, 9.69, 9.16, 9.16, 9.63, 9.71, 9.51, 9.59, 9.43, 9.33, 8.63, 7.74, 7.92, 7.61, 7.57, 7.44, 7.3, 7.18, 7.01, 6.89, 6.84, 7.16, 6.93, 6.6, 6.36, 6.53, 6.73], 1: [4.73, 5.1, 4.88, 4.57, 4.79, 5.12, 4.35, 4.31, 4.92, 4.85, 4.83, 4.54, 4.71, 4.69, 4.62, 4.85, 5.31, 5.23, 5.7, 5.64, 5.55, 5.73, 5.38, 5.0, 4.8, 4.49, 4.49, 4.91, 4.87, 4.68, 4.57], 2: [5.01, 5.13, 5.57, 5.8, 6.17, 6.34, 5.96, 6.11, 5.77, 5.19, 5.27, 5.54, 4.76, 4.76, 4.9, 4.73, 5.04, 5.31, 5.24, 5.4, 5.3, 5.46, 6.0, 6.16, 6.27, 6.39, 6.52, 7.03], 3: [6.9, 6.85, 6.78, 6.65, 6.61, 6.97, 7.46, 7.83, 7.96, 8.03, 8.45, 8.47, 8.3, 8.65, 9.04, 9.32, 9.24, 9.32, 9.12, 9.34, 9.39, 9.23, 9.38, 9.63, 9.51, 9.23, 9.26, 9.23, 9.37, 9.35, 9.9]}
# Série de décembre reprise du code V1 fourni dans la conversation.
NORMS[12]=[6.53,6.41,6.42,6.63,6.79,6.20,5.84,5.96,5.72,5.35,5.26,5.37,5.28,4.72,4.62,5.27,5.48,5.59,5.75,5.45,5.51,5.61,5.41,5.49,5.39,4.85,4.71,4.58,4.61,4.96,4.81]
def norm_temp(d):
    ns=NORMS.get(d.month)
    return ns[min(d.day,len(ns))-1] if ns else None


def get_weather(today):
    if not ENABLE_METEO: return {}
    data=api_json('https://api.open-meteo.com/v1/forecast',params={'latitude':','.join(str(x[0]) for x in STATIONS),'longitude':','.join(str(x[1]) for x in STATIONS),'daily':'temperature_2m_min,temperature_2m_max','timezone':'Europe/Paris','forecast_days':7})
    if isinstance(data,dict): data=[data]
    if len(data)!=len(STATIONS): raise ValueError('Nombre de stations météo inattendu')
    sums=defaultdict(float); weights=defaultdict(float)
    for station,(_,_,weight) in zip(data,STATIONS):
        x=station.get('daily',{})
        for day,lo,hi in zip(x.get('time',[]),x.get('temperature_2m_min',[]),x.get('temperature_2m_max',[])):
            if lo is None or hi is None: continue
            d=dt.date.fromisoformat(day); sums[d]+=(lo+hi)*.5*weight; weights[d]+=weight
    return {d:(round(sums[d]/weights[d],2),norm_temp(d)) for d in sums if weights[d]>0}


def read_args(args,today):
    # Existing HA invocation: RED WHITE BLUE [official tomorrow colour].
    if len(args)<3: raise ValueError('Arguments requis : rouges_restants blancs_restants bleus_restants [couleur_J+1]')
    stocks=tuple(int(x) for x in args[:3]); red,white,blue=stocks
    sy=season_start(today); total=(dt.date(sy.year+1,9,1)-sy).days
    if not (0<=red<=22 and 0<=white<=43 and 0<=blue<=total-65): raise ValueError('Stocks Tempo invalides')
    c=args[3].strip().lower() if len(args)>3 else None
    c={'red':'rouge','white':'blanc','blue':'bleu'}.get(c,c)
    return stocks,c if c in COLORS else None


def normalize_stocks(today,stocks,tempo):
    """Return stock at end of today, not the stock of unplanned days from HA."""
    start=season_start(today); end=dt.date(start.year+1,9,1)
    past=list(date_range(start,today+dt.timedelta(days=1)))
    if all(d in tempo for d in past):
        counts=Counter(tempo[d] for d in past)
        actual=(22-counts['rouge'],43-counts['blanc'],(end-start).days-65-counts['bleu'])
        if min(actual)>=0:
            return actual,'Tempo_history',[]
    n=(end-today).days-1
    if sum(stocks)==n:
        return stocks,'HA_end_today',[]
    tomorrow=today+dt.timedelta(days=1)
    if sum(stocks)==n-1 and tomorrow<end and tempo.get(tomorrow) in COLORS:
        fixed=list(stocks); fixed[{'rouge':0,'blanc':1,'bleu':2}[tempo[tomorrow]]]+=1
        caps=(22,43,(end-start).days-65)
        if all(x<=cap for x,cap in zip(fixed,caps)):
            return tuple(fixed),'HA_tomorrow_already_planned',['Stock HA ajusté : la couleur publiée de demain était déjà décomptée.']
    if sum(stocks)==n+1 and tempo.get(today) in COLORS:
        fixed=list(stocks); i={'rouge':0,'blanc':1,'bleu':2}[tempo[today]]
        if fixed[i]>0:
            fixed[i]-=1
            return tuple(fixed),'HA_today_included',['Stock HA ajusté : aujourd’hui était encore inclus.']
    return stocks,'HA_unverified',['Stock HA non réconcilié avec le calendrier ; vérifier la date de référence.']


def get_tempo_calendar(token,start,end):
    try:
        return api_json(TEMPO_URL,token,{**params_dates(start,end),'fallback_status':'false'})
    except HTTPError as exc:
        # Before tomorrow is published, the requested end can be rejected.
        # Retry with today inclusive rather than losing the historical calendar.
        if exc.code!=400 or (end-start).days<=1: raise
        return api_json(TEMPO_URL,token,{**params_dates(start,end-dt.timedelta(days=1)),'fallback_status':'false'})


def build_forecast(stocks,real_j1=None,now=None):
    now=now or dt.datetime.now(PARIS); today=now.astimezone(PARIS).date(); token=get_token(); errors=[]
    start=pp_start(today+dt.timedelta(days=1)); end=today+dt.timedelta(days=7)
    if today.month==10: start=dt.date(today.year,11,1)
    # Only request current winter history when in or close to its season.
    need_pp=any(eligible(d) for d in date_range(today+dt.timedelta(days=1),end)) or today.month in TUNNEL
    tasks={'short':lambda:api_json(SHORT_URL,token), 'week':lambda:api_json(WEEK_URL,token),
           'gen':lambda:api_json(GEN_URL,token,{'type':'D-1,D-2,D-3'}),
           'annual_prev':lambda:api_json(ANNUAL_URL,token,params_dates(dt.date(today.year-1,1,1),dt.date(today.year,1,1))),
           'annual':lambda:api_json(ANNUAL_URL,token,params_dates(dt.date(today.year,1,1),dt.date(today.year+1,1,1))),
           'tempo':lambda:get_tempo_calendar(token,season_start(today),min(today+dt.timedelta(days=2),dt.date(season_start(today).year+1,9,1)))}
    boundary=dt.date(season_start(today).year+1,9,1)
    if today+dt.timedelta(days=2)>boundary: tasks['tempo_next']=lambda:get_tempo_calendar(token,boundary,today+dt.timedelta(days=2))
    if need_pp: tasks['pp']=lambda:api_json(SIGNAL_URL,token,params_dates(max(start,dt.date(2026,11,1)),end))
    if ENABLE_METEO: tasks['weather']=lambda:get_weather(today)
    if ENABLE_ENR_METEO: tasks['enr_weather']=lambda:get_enr_weather(today)
    data={}
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={k:pool.submit(f) for k,f in tasks.items()}
        for k,job in jobs.items():
            try: data[k]=job.result()
            except Exception as exc:
                errors.append(f'{k}: {type(exc).__name__}: {exc}'); data[k]={}
    try: signals=parse_signals(data.get('pp',{})); signal_ok=not need_pp or isinstance(data.get('pp',{}).get('signals'),(list,dict))
    except Exception as exc: signals={}; signal_ok=False; errors.append(f'PP parse: {exc}')
    # Ignore non-opposable historic data and any update dated after this execution.
    signals={d:r for d,r in signals.items() if d>=dt.date(2026,11,1) and r[1]<=now}
    try: tempo={**parse_tempo(data['tempo']),**parse_tempo(data.get('tempo_next',{}))}
    except Exception as exc: tempo={}; errors.append(f'Tempo parse: {exc}')
    tomorrow=today+dt.timedelta(days=1)
    if real_j1 in COLORS and tomorrow not in tempo and now.astimezone(PARIS).time()>=dt.time(10,30): tempo[tomorrow]=real_j1
    stocks,tempo_stock_source,stock_warnings=normalize_stocks(today,stocks,tempo)
    errors.extend(stock_warnings)
    enr_weather=data.get('enr_weather',{})
    errors.extend(enr_weather.get('warnings',[]))
    metrics=daily_metrics(today,data['short'],data['week'],data['gen'],now,enr_weather)
    annual=annual_daily([data['annual_prev'],data['annual']]); matrices=Thresholds()
    result,errors=forecast(today,stocks,metrics,signals,tempo,matrices,annual,data.get('weather',{}),signal_ok,errors)
    errors += [f'Matrice {k}: {v}' for k,v in matrices.errors.items()]
    return {'generated_at':now.isoformat(),'version':'2.1.0',**result,'diagnostics':{'warnings':errors,'matrices_loaded':[f'{k[0]}_j{k[1]}' for k,v in matrices.cache.items() if v],'probabilities_calibrated':False,'enr_model':{'enabled':ENABLE_ENR_METEO,'land_points':len(ENR_LAND),'sea_points':len(ENR_SEA),'capacity_mw':ENR_CAPACITY,'weather_hours_by_component':{k:len(v) for k,v in enr_weather.get('series',{}).items()},'calibrated':False},'tempo_stock_source':tempo_stock_source,'tempo_stock_at_end_today':dict(zip(('rouge','blanc','bleu'),stocks))},'error':None}


def main():
    try:
        now=dt.datetime.now(PARIS); stocks,color=read_args(sys.argv[1:],now.date())
        print(json.dumps(build_forecast(stocks,color,now),ensure_ascii=False,allow_nan=False))
    except Exception as exc:
        # Preserve JSON on stdout; never expose OAuth credentials or headers.
        print(json.dumps({'generated_at':dt.datetime.now(PARIS).isoformat(),'version':'2.1.0','error':f'{type(exc).__name__}: {exc}',**{f'J+{i}':None for i in range(1,7)}},ensure_ascii=False))
        print(f'TEMPO V2: {type(exc).__name__}: {exc}',file=sys.stderr)

if __name__=='__main__': main()
