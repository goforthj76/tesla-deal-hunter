import asyncio
import math
from datetime import datetime, timezone, timedelta
import hashlib
import logging
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from .database import ROOT, Base, engine, Session, Vehicle, Listing, Price, Source, Activity, Setting, Hunt, now, migrate_legacy_database
from .analysis import assess, ranking_key
from .collectors import autohub, marketcheck, dallas

load_dotenv(ROOT/'.env')
log=logging.getLogger('hunter'); log.setLevel(logging.INFO)
handler=RotatingFileHandler(ROOT/'data/scanner.log',maxBytes=1_000_000,backupCount=3)
log.addHandler(handler)
logging.getLogger('httpx').setLevel(logging.WARNING)

class Preferences(BaseModel):
    latitude: float = Field(default=33.0146,ge=-90,le=90)
    longitude: float = Field(default=-97.0969,ge=-180,le=180)
    radius: int = Field(default=50,ge=1,le=500)
    max_price: int = Field(default=36000,ge=1000,le=200000)
    ideal_max: int = Field(default=31000,ge=1000,le=200000)
    preferred_mileage: int = Field(default=50000,ge=0,le=300000)
    models: list[str] = Field(default=['Model Y','Model 3'],min_length=1,max_length=2)
    require_hw4: bool = True
    dealer_interval_minutes: int = Field(default=360,ge=60,le=1440)
    api_interval_minutes: int = Field(default=1440,ge=360,le=10080)
    monthly_api_budget: int = Field(default=450,ge=1,le=500)
    fsd_monthly: float = Field(default=99,ge=0,le=1000)
    ownership_months: int = Field(default=36,ge=1,le=120)

started=time.time(); task=None; scanning=None
scan_metrics={}
MARKETCHECK_ARCHIVE_ENABLED = os.getenv("MARKETCHECK_RETENTION_AUTHORIZED", "").lower() == "true"
def activity(message):
    with Session.begin() as s: s.add(Activity(message=message))
    log.info(json.dumps({'at':now(),'message':message}))

def settings():
    with Session() as s: return s.get(Setting,1).data

def ingest(source, rows, complete):
    stamp=now(); seen=set()
    with Session.begin() as s:
        for d in rows:
            vin=d['vin']; key=hashlib.sha256((source+'|'+d['url']).encode()).hexdigest(); seen.add(key)
            vehicle=s.get(Vehicle,vin)
            if not vehicle:
                vehicle=Vehicle(vin=vin,first_seen=stamp,last_seen=stamp);s.add(vehicle)
                s.add(Activity(message='New VIN: '+vin))
            vehicle.last_seen=stamp
            listing=s.get(Listing,key)
            old=listing.data.get('price') if listing else None
            if not listing:
                listing=Listing(id=key,vin=vin,source=source,url=d['url'],data=d,first_seen=stamp,last_seen=stamp);s.add(listing)
            listing.data=d; listing.last_seen=stamp;listing.missing=0;listing.status='ACTIVE'
            if d.get('price') is not None and old!=d['price']:
                s.add(Price(listing_id=key,vin=vin,amount=d['price'],observed=stamp))
                if old is not None: s.add(Activity(message=f'Price change {vin}: {old} → {d["price"]}'))
            s.flush()
        if complete:
            for listing in s.scalars(select(Listing).where(Listing.source==source)):
                if listing.id not in seen:
                    listing.missing+=1
                    listing.status='NOT SEEN' if listing.missing<2 else 'REMOVED / AVAILABILITY UNKNOWN'
        src=s.get(Source,source);src.observations+=len(rows);src.scans+=1;src.last_success=stamp;src.status='HEALTHY' if complete else 'DEGRADED';src.error='' if complete else 'Pagination capped; incomplete coverage'
        # Keep the UI activity buffer bounded; price history is retained.
        cutoff=s.scalar(select(Activity.id).order_by(Activity.id.desc()).offset(2000).limit(1))
        if cutoff: s.execute(delete(Activity).where(Activity.id<cutoff))

def reserve_api():
    with Session.begin() as s:
        src=s.get(Source,'MarketCheck'); month=now()[:7]
        if src.budget_month!=month: src.request_count=0;src.budget_month=month
        if src.request_count>=s.get(Setting,1).data['monthly_api_budget']: raise ValueError('Local monthly API budget exhausted')
        src.request_count+=1

async def scan_source(name):
    global scanning
    if name == "MarketCheck" and not MARKETCHECK_ARCHIVE_ENABLED:
        activity("MarketCheck scheduled archive disabled: retention authorization required")
        return
    scanning=name; pref=settings(); scan_started=time.time()
    before_calls=0
    with Session() as s: before_calls=s.get(Source,name).request_count
    activity('Starting '+name)
    with Session.begin() as s: s.get(Source,name).status='SCANNING'
    try:
        if name=='Autohub Texas': rows,complete=await autohub(activity)
        elif name=='Autos of Dallas': rows,complete=await dallas(activity)
        else: rows,complete=await marketcheck(os.getenv('MARKETCHECK_API_KEY',''),pref,activity,reserve_api)
        ingest(name,rows,complete)
        scan_metrics[name]={'last_count':len(rows),'duration_seconds':round(time.time()-scan_started,2),'complete':complete}
        with Session.begin() as s:
            metric=s.get(Setting,2)
            data=dict(metric.data) if metric else {}
            data[name]=scan_metrics[name]
            if metric: metric.data=data
            else: s.add(Setting(id=2,data=data))
        activity(f'{name}: completed, {len(rows)} listing observations')
    except asyncio.CancelledError: raise
    except Exception as exc:
        # Error types are useful without leaking API URLs/secrets.
        safe=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
        with Session.begin() as s:
            src=s.get(Source,name);src.status='UNAVAILABLE';src.error=safe[:200]
        activity(name+': unavailable — '+safe[:200])
    finally:
        with Session.begin() as s:
            src=s.get(Source,name)
            interval=pref['dealer_interval_minutes'] if name!='MarketCheck' else pref['api_interval_minutes']
            if name=='MarketCheck':
                remaining=max(0,pref['monthly_api_budget']-src.request_count)
                used=max(1,src.request_count-before_calls)
                today=datetime.now(timezone.utc)
                next_month=(today.replace(day=28)+timedelta(days=4)).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
                seconds_left=(next_month-today).total_seconds()
                interval=max(interval,1440,math.ceil(seconds_left*used/max(remaining,1)/60))
            src.next_scan=time.time()+interval*60
        scanning=None

def hunt_counts():
    from sqlalchemy import func
    with Session() as s:
        return dict(observations=s.scalar(select(func.coalesce(func.sum(Source.observations),0))),unique_vins=s.scalar(select(func.count()).select_from(Vehicle)),scans=s.scalar(select(func.coalesce(func.sum(Source.scans),0))))


# Search discovery is separate from vehicle inventory. Never fetch result URLs here.
import threading
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import httpx
_discovery_lock=threading.Lock()
DISCOVERY_TOWNS=['Flower Mound','Lewisville','Denton','Grapevine','Coppell','Southlake','Keller','Irving','Plano','Fort Worth','Dallas','Frisco']

def canonical_discovery_url(value):
    try:
        u=urlsplit(value)
        if u.scheme not in ['http','https'] or not u.hostname or u.username or u.password: return None
        query=urlencode(sorted((k,v) for k,v in parse_qsl(u.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ['gclid','fbclid']))
        return urlunsplit((u.scheme,u.netloc.lower(),u.path or '/',query,''))
    except ValueError: return None

def discovery_state():
    with Session() as session:
        row=session.get(Setting,3)
        data=dict(row.data) if row else {}
    if data.get('month')!=now()[:7]: data.update(month=now()[:7],reserved_calls=0)
    data.setdefault('leads',[])
    data.setdefault('next_scan',0)
    data.setdefault('cursor',0)
    data['configured']=bool(os.getenv('TAVILY_API_KEY'))
    data['monthly_cap']=300
    data['remaining']=max(0,300-data.get('reserved_calls',0))
    return data

def save_discovery(data):
    with Session.begin() as session:
        row=session.get(Setting,3)
        if row: row.data=data
        else: session.add(Setting(id=3,data=data))

def discover():
    if not _discovery_lock.acquire(blocking=False): return {'status':'already running'}
    try:
        data=discovery_state()
        if not data['configured']: return {'status':'API key not configured'}
        if data['next_scan']>time.time(): return {'status':'cooldown','next_scan':data['next_scan']}
        if not data['remaining']: return {'status':'local monthly cap reached'}
        town=DISCOVERY_TOWNS[data['cursor']%len(DISCOVERY_TOWNS)]
        query=f'used Tesla Model 3 Model Y for sale dealer {town} Texas'
        data.update(reserved_calls=data.get('reserved_calls',0)+1,cursor=data['cursor']+1,next_scan=time.time()+4*3600,status='SEARCHING',last_query=query)
        save_discovery(data) # Reserve before network I/O, including failures/restarts.
        try:
            response=httpx.post('https://api.tavily.com/search',headers={'Authorization':'Bearer '+os.environ['TAVILY_API_KEY']},json={'query':query,'search_depth':'basic','auto_parameters':False,'max_results':10,'include_answer':False,'include_raw_content':False,'include_images':False},timeout=30)
            if response.status_code!=200: raise ValueError('Tavily HTTP '+str(response.status_code))
            payload=response.json()
            if not isinstance(payload.get('results'),list): raise ValueError('Unexpected Tavily response')
            leads={x['url']:x for x in data['leads']};new_count=0
            for item in payload['results']:
                url=canonical_discovery_url(item.get('url',''))
                if not url: continue
                prior=leads.get(url);new_count+=int(prior is None)
                leads[url]={'url':url,'title':str(item.get('title',''))[:300],'snippet':str(item.get('content',''))[:1200],'first_seen':prior['first_seen'] if prior else now(),'last_seen':now(),'town_query':town,'status':'UNVERIFIED LEAD — distance, availability and VIN need review'}
            data.update(leads=sorted(leads.values(),key=lambda x:x['last_seen'],reverse=True)[:2000],status='HEALTHY',last_success=now(),last_new_urls=new_count,last_results=len(payload['results']),error='')
            activity(f'Tavily discovery: {len(payload["results"])} results, {new_count} new URLs; not verified vehicles')
        except Exception as exc:
            data.update(status='UNAVAILABLE',error=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
        save_discovery(data)
        return {'status':data['status'],'new_urls':data.get('last_new_urls',0)}
    finally: _discovery_lock.release()

async def worker():
    while True:
        try:
            if os.getenv('TAVILY_API_KEY'): await asyncio.to_thread(discover)
            for name in ['Autohub Texas','Autos of Dallas','MarketCheck']:
                if name=='MarketCheck' and (not MARKETCHECK_ARCHIVE_ENABLED or not os.getenv('MARKETCHECK_API_KEY')): continue
                with Session() as s: due=s.get(Source,name).next_scan<=time.time()
                if due: await scan_source(name)
        except asyncio.CancelledError: raise
        except Exception as exc: activity('Worker recovered from '+type(exc).__name__)
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app):
    global task
    migrate_legacy_database()
    Base.metadata.create_all(engine)
    with Session.begin() as s:
        if not s.get(Setting,1):s.add(Setting(id=1,data=Preferences().model_dump()))
        # One-time merge migration to the user's confirmed preferences.
        if not s.get(Setting,4):
            st=s.get(Setting,1)
            if st:
                pref=dict(st.data);pref.update(radius=50,max_price=36000,ideal_max=31000,preferred_mileage=50000,models=['Model 3','Model Y'],require_hw4=True,dealer_interval_minutes=360,api_interval_minutes=1440);st.data=pref
            s.add(Setting(id=4,data={'unified_preferences':True}))
        for name in ['Autohub Texas','Autos of Dallas','MarketCheck']:
            if not s.get(Source,name):s.add(Source(name=name))
        s.flush()
        if not os.getenv('MARKETCHECK_API_KEY'):
            src=s.get(Source,'MarketCheck');src.status='NOT CONFIGURED';src.error='Optional API key absent. No calls made.'
    if not MARKETCHECK_ARCHIVE_ENABLED:
        with Session.begin() as s:
            src=s.get(Source,'MarketCheck');src.status='DISABLED';src.error='Scheduled archiving disabled pending retention authorization. Historical records are preserved, not refreshed.'
    with Session() as s:
        metric=s.get(Setting,2)
        if metric: scan_metrics.update(metric.data)
    activity('Scanner process started; persisted schedules restored')
    task=asyncio.create_task(worker())
    yield
    task.cancel()
    try:await task
    except asyncio.CancelledError:pass
    activity('Scanner stopped gracefully')

app=FastAPI(title='Tesla Deal Hunter',lifespan=lifespan)

@app.middleware('http')
async def security(request:Request,call_next):
    import base64
    password=os.getenv('TDH_PASSWORD','')
    if password:
        try:
            value=base64.b64decode(request.headers.get('authorization','').split(' ',1)[1]).decode()
            valid=secrets.compare_digest(value,'hunter:'+password)
        except Exception:valid=False
        if not valid:return JSONResponse({'detail':'Sign in as hunter'},401,headers={'WWW-Authenticate':'Basic realm="Tesla Deal Hunter"'})
    elif request.client.host not in ['127.0.0.1','::1','testclient']:
        return JSONResponse({'detail':'Set TDH_PASSWORD for LAN access'},403)
    if request.method not in ['GET','HEAD','OPTIONS']:
        origin=request.headers.get('origin')
        if origin and origin!=str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'Cross-origin write rejected'},403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https:; connect-src 'self'; frame-ancestors 'none'"
    return response

def vehicle_rows():
    pref=settings(); result=[]
    with Session() as s:
        listings=list(s.scalars(select(Listing))); grouped={}
        for l in listings:grouped.setdefault(l.vin,[]).append(l)
        for v in s.scalars(select(Vehicle)):
            records=grouped.get(v.vin,[])
            if not records:continue
            records.sort(key=lambda l:(l.source!='MarketCheck' or MARKETCHECK_ARCHIVE_ENABLED,l.last_seen),reverse=True)
            active=[l for l in records if l.status=='ACTIVE']
            chosen=(active or records)[0];d=dict(chosen.data)
            analysis=assess(d,pref)
            historical_only=all(l.source=='MarketCheck' for l in (active or records)) and not MARKETCHECK_ARCHIVE_ENABLED
            if historical_only: analysis['risks'].append('Historical MarketCheck snapshot; availability and price are not refreshed')
            prices={l.data.get('price') for l in active if l.data.get('price') is not None}
            if len(prices)>1:
                analysis['risks'].append('Conflicting advertised prices across sources')
                if analysis['category']!='REJECTED':analysis['category']='MANUAL REVIEW'
            result.append(dict(**d,**analysis,historical_only=historical_only,watched=v.watched,first_seen=v.first_seen,last_seen=v.last_seen,status=chosen.status,source_count=len(records),sources=[dict(name=l.source,url=l.url,observed=l.last_seen,status=l.status,price=l.data.get('price')) for l in records]))
    return sorted(result,key=ranking_key)

@app.get('/api/state')
def state():
    rows=vehicle_rows()
    with Session() as s:
        sources=[dict(name=x.name,status=x.status,error=x.error,last_success=x.last_success,next_scan=x.next_scan,scans=x.scans,observations=x.observations,api_calls=x.request_count) for x in s.scalars(select(Source))]
        events=[dict(at=x.at,message=x.message) for x in s.scalars(select(Activity).order_by(Activity.id.desc()).limit(80))]
    for source in sources:
        source.update(scan_metrics.get(source['name'],{}))
    projected=sum(x.get('last_count',0)*3600/max(86400 if x['name']=='MarketCheck' else settings()['dealer_interval_minutes']*60, x['next_scan']-datetime.fromisoformat(x['last_success']).timestamp() if x['last_success'] else 86400) for x in sources if x['status'] not in ['DISABLED','NOT CONFIGURED','UNAVAILABLE'])
    dealers=sorted({r.get('dealer') for r in rows if r.get('dealer')})
    with Session() as s:
        hunt=s.scalar(select(Hunt).order_by(Hunt.id.desc()).limit(1))
        hunt_info=None if not hunt else dict(id=hunt.id,started_at=hunt.started_at,ended_at=hunt.ended_at,delta=hunt.summary if hunt.ended_at else {k:v-hunt.baseline.get(k,0) for k,v in hunt_counts().items()})
    return dict(telegram_configured=bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID')),hunt=hunt_info,dealers=dealers,projected_checks_per_hour=round(projected,1),api_budget_remaining=max(0,settings()['monthly_api_budget']-next((x['api_calls'] for x in sources if x['name']=='MarketCheck'),0)),vehicles=rows,sources=sources,activity=events,settings=settings(),uptime=int(time.time()-started),scanning=scanning,stats=dict(listings_scanned=sum(x['observations'] for x in sources),unique_vins=len(rows),scans_completed=sum(x['scans'] for x in sources),history_checks=0,auction_checks=0),limitations='MarketCheck historical snapshot is not being refreshed; scheduled API archiving is disabled pending retention permission. Live inventory from enabled dealer sources only. Unknown distances require review. Seller history, HW4 and purchased FSD need verification. Market-wide coverage and phone push alerts are not yet available.')

@app.get('/api/vehicles/{vin}/prices')
def prices(vin:str):
    with Session() as s:return [dict(amount=x.amount,observed=x.observed,listing_id=x.listing_id) for x in s.scalars(select(Price).where(Price.vin==vin).order_by(Price.id))]

@app.post('/api/vehicles/{vin}/watch')
def watch(vin:str):
    with Session.begin() as s:
        v=s.get(Vehicle,vin)
        if not v:raise HTTPException(404)
        v.watched=not v.watched
        return {'watched':v.watched}

@app.put('/api/settings')
def update_preferences(p:Preferences):
    if any(m not in ['Model 3','Model Y'] for m in p.models):raise HTTPException(422,'Unsupported model')
    if p.ideal_max>p.max_price:raise HTTPException(422,'Ideal maximum must not exceed maximum price')
    with Session.begin() as s:s.get(Setting,1).data=p.model_dump()
    activity('Search preferences updated; existing schedules retained')
    return p

@app.post('/api/telegram/test')
async def telegram_test():
    from .notifier import test_connection
    return await test_connection()

@app.get('/api/research-seeds')
def research_seeds():
    return json.loads((ROOT/'app/research_seeds.json').read_text(encoding='utf-8'))

@app.get('/api/discovery')
def get_discovery():
    return discovery_state()

@app.post('/api/discovery/run')
def run_discovery():
    return discover()

@app.post('/api/hunt/start')
def start_hunt():
    with Session.begin() as s:
        active=s.scalar(select(Hunt).where(Hunt.ended_at.is_(None)))
        if active: return {'id':active.id,'status':'already running'}
        h=Hunt(baseline=hunt_counts());s.add(h);s.flush();identifier=h.id
    activity('Overnight report started; existing source schedules and limits remain in effect')
    return {'id':identifier,'status':'running'}

@app.post('/api/hunt/finish')
def finish_hunt():
    current=hunt_counts()
    with Session.begin() as s:
        h=s.scalar(select(Hunt).where(Hunt.ended_at.is_(None)))
        if not h: raise HTTPException(409,'No active hunt report')
        h.ended_at=now();h.summary={k:v-h.baseline.get(k,0) for k,v in current.items()}
        result=h.summary
    activity('Overnight report finished; regular scanner schedule continues')
    return result

app.mount('/static' ,StaticFiles(directory=ROOT/'app/static'),name='static')
@app.get('/')
def home():return FileResponse(ROOT/'app/static/index.html')
