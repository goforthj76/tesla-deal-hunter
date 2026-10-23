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
from .analysis import assess, ranking_key, normalize_vin
from .database import Evidence, Observation
from .investigator import investigate, FIELDS, KINDS
from .expanded import CAP, BATCH, INTERVAL, plans, relevant, inspect_dealers, SEEDS, choose_vin_task, vin_result_matches
from .collectors import autohub, marketcheck, dallas, tesla_used, cars_com, cargurus, carvana_search, truecar_search, scan_all_sources
from .scoring import score_deal, compute_market_averages, check_hw4

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
    validated=[]
    for row in rows:
        try:
            row=dict(row);row['vin']=normalize_vin(row.get('vin'))
            price=row.get('price');mileage=row.get('mileage')
            if price is not None and (not isinstance(price,(int,float)) or not math.isfinite(price) or price<5000):raise ValueError('Invalid price')
            if mileage is not None and (not isinstance(mileage,(int,float)) or not math.isfinite(mileage) or mileage<0):raise ValueError('Invalid mileage')
            validated.append(row)
        except (ValueError,TypeError):complete=False
    rows=validated
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
            changed=listing is None or listing.data!=d
            if listing and changed:
                s.add(Observation(vin=vin,source=source,url=listing.url,data=dict(listing.data),observed=listing.last_seen))
            if not listing:
                listing=Listing(id=key,vin=vin,source=source,url=d['url'],data=d,first_seen=stamp,last_seen=stamp);s.add(listing)
            if changed:
                s.add(Observation(vin=vin,source=source,url=d['url'],data=dict(d),observed=stamp))
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
        src = s.get(Source, source)
        if not src:
            src = Source(name=source)
            s.add(src)
        src.observations += len(rows)
        src.scans += 1
        src.last_success = stamp
        src.status = 'HEALTHY' if complete else 'DEGRADED'
        src.error = '' if complete else 'Partial coverage; missing records are not marked sold'
        # Keep the UI activity buffer bounded; price history is retained.
        cutoff=s.scalar(select(Activity.id).order_by(Activity.id.desc()).offset(2000).limit(1))
        if cutoff: s.execute(delete(Activity).where(Activity.id<cutoff))

def reserve_api():
    with Session.begin() as s:
        src=s.get(Source,'MarketCheck'); month=now()[:7]
        if src.budget_month!=month: src.request_count=0;src.budget_month=month
        if src.request_count>=s.get(Setting,1).data['monthly_api_budget']: raise ValueError('Local monthly API budget exhausted')
        src.request_count+=1

SOURCE_NAMES=['Autohub Texas','Autos of Dallas','Tesla.com','Cars.com','CarGurus','Carvana','TrueCar','Discovered dealers','MarketCheck']
_source_locks={}
async def scan_source(name):
    if name not in SOURCE_NAMES: raise HTTPException(404,'Unknown source')
    lock=_source_locks.setdefault(name,asyncio.Lock())
    if lock.locked(): return
    async with lock: await _scan_source(name)

async def _scan_source(name):
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
        if name == 'Autohub Texas': rows, complete = await autohub(activity)
        elif name == 'Autos of Dallas': rows, complete = await dallas(activity)
        elif name == 'TrueCar': rows, complete = await truecar_search(pref, activity)
        elif name == 'Carvana': rows, complete = await carvana_search(pref, activity)
        elif name == 'Tesla.com': rows, complete = await tesla_used(pref, activity)
        elif name == 'Cars.com': rows, complete = await cars_com(pref, activity)
        elif name == 'CarGurus': rows, complete = await cargurus(pref, activity)
        elif name == 'Discovered dealers':
            data=discovery_state()
            with Session() as session:
                checkpoint=session.get(Setting,5);cursor=checkpoint.data.get('cursor',0) if checkpoint else 0
            from .expanded import allowed
            urls=list(dict.fromkeys(x['url'] for x in data['leads']+SEEDS if allowed(x['url'])))
            ordered=urls[cursor%len(urls):]+urls[:cursor%len(urls)] if urls else []
            rows,checks=await inspect_dealers(ordered,activity);complete=False
            with Session.begin() as session:
                checkpoint=session.get(Setting,5)
                details={'cursor':cursor+min(24,len(urls)),'last_checks':checks,'queued_urls':len(urls)}
                if checkpoint:checkpoint.data=details
                else:session.add(Setting(id=5,data=details))
            if not checks['read']:raise ValueError('No dealer pages readable')
        else: rows, complete = await marketcheck(os.getenv('MARKETCHECK_API_KEY', ''), pref, activity, reserve_api)
        if not rows:raise ValueError("No vehicle records returned; empty inventory not independently verified")
        if name not in ['Autohub Texas','Autos of Dallas']:complete=False
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
    data['monthly_cap']=CAP
    data['queries_per_cycle']=BATCH
    data['max_results_per_query']=20
    data['query_plan_size']=len(plans())
    data['result_slots_per_hour']=BATCH*20*3600/INTERVAL
    data['remaining']=max(0,CAP-data.get('reserved_calls',0))
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
        plan=plans();leads={x['url']:x for x in data['leads'] if relevant(x) or x.get('vin')}
        with Session() as session:
            candidates=[dict(l.data,vin=l.vin) for l in session.scalars(select(Listing).where(Listing.status=='ACTIVE'))]
        attempts=dict(data.get('vin_attempts',{}))
        data.update(next_scan=time.time()+INTERVAL,status='SEARCHING')
        new_count=0;result_count=0;filtered=0;errors=[];completed=0
        for _ in range(min(BATCH,data['remaining'])):
            vin=choose_vin_task(candidates,attempts,time.time()) if data['cursor']%4==3 else None
            item={'query':'"'+vin+'" Tesla history auction salvage dealer location','label':'VIN '+vin,'domains':[]} if vin else plan[(data['cursor']-data['cursor']//4)%len(plan)]
            if vin:
                attempts[vin]=time.time();data['vin_attempts']=attempts
            data.update(reserved_calls=data.get('reserved_calls',0)+1,cursor=data['cursor']+1,last_query=item['query'])
            save_discovery(data) # Each request reserved before I/O, including errors.
            try:
                request={'query':item['query'],'search_depth':'basic','auto_parameters':False,'max_results':20,'include_answer':False,'include_raw_content':False,'include_images':False}
                if item['domains']:request['include_domains']=item['domains']
                response=httpx.post('https://api.tavily.com/search',headers={'Authorization':'Bearer '+os.environ['TAVILY_API_KEY']},json=request,timeout=30)
                if response.status_code!=200:raise ValueError('Tavily HTTP '+str(response.status_code))
                payload=response.json()
                if not isinstance(payload.get('results'),list):raise ValueError('Unexpected Tavily response')
                completed+=1;result_count+=len(payload['results'])
                runs=list(data.get('query_runs',[]))
                runs.append({'query':item['query'],'label':item['label'],'at':now(),'results':len(payload['results']),'vin':vin})
                data['query_runs']=runs[-120:]
                for result in payload['results']:
                    url=canonical_discovery_url(result.get('url',''))
                    if not url or not (vin_result_matches(result,vin) if vin else relevant(result)):filtered+=1;continue
                    prior=leads.get(url);new_count+=int(prior is None)
                    leads[url]={'url':url,'title':str(result.get('title',''))[:300],'snippet':str(result.get('content',''))[:1200],'first_seen':prior['first_seen'] if prior else now(),'last_seen':now(),'town_query':item['label'],'vin':vin,'status':'UNVERIFIED LEAD — distance, availability and VIN need review'}
                data.update(leads=sorted(leads.values(),key=lambda x:x['last_seen'],reverse=True)[:2000])
                save_discovery(data)
            except Exception as exc:
                errors.append(str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
                if getattr(locals().get('response'),'status_code',None) in [401,429,432,433]:break
        data.update(status='HEALTHY' if not errors else 'DEGRADED' if completed else 'UNAVAILABLE',last_new_urls=new_count,last_results=result_count,last_filtered=filtered,last_queries=completed,error='; '.join(errors)[:200])
        if completed:data['last_success']=now()
        save_discovery(data)
        activity(f'Tavily expanded discovery: {completed} queries, {result_count} results, {new_count} new URLs, {filtered} filtered; not verified vehicles')
        return {'status':data['status'],'new_urls':new_count}

    finally: _discovery_lock.release()

async def worker():
    while True:
        try:
            if os.getenv('TAVILY_API_KEY'): await asyncio.to_thread(discover)
            for name in ['Autohub Texas','Autos of Dallas','Discovered dealers','MarketCheck']:
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
        for name in ['Autohub Texas','Autos of Dallas','Discovered dealers','MarketCheck']:
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
    if not password:
        return JSONResponse({'detail':'Access disabled until TDH_PASSWORD is configured locally'},503)
    if password:
        try:
            value=base64.b64decode(request.headers.get('authorization','').split(' ',1)[1]).decode()
            valid=secrets.compare_digest(value,'hunter:'+password)
        except Exception:valid=False
        if not valid:return JSONResponse({'detail':'Sign in as hunter'},401,headers={'WWW-Authenticate':'Basic realm="Tesla Deal Hunter"'})

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
    pref = settings()
    result = []
    with Session() as s:
        listings = list(s.scalars(select(Listing)))
        evidence_by_vin = {}
        for e in s.scalars(select(Evidence).where(Evidence.retracted==False)):
            evidence_by_vin.setdefault(e.vin,[]).append(dict(field=e.field,value=e.value,kind=e.kind,source=e.source,url=e.url,observed=e.observed))
        grouped = {}
        for l in listings:
            grouped.setdefault(l.vin, []).append(l)

        all_data = [l.data for l in listings if l.data]
        market_comps = compute_market_averages(all_data)

        for v in s.scalars(select(Vehicle)):
            records = grouped.get(v.vin, [])
            if not records:
                continue
            records.sort(key=lambda l: (l.source != 'MarketCheck' or MARKETCHECK_ARCHIVE_ENABLED, l.last_seen), reverse=True)
            active = [l for l in records if l.status == 'ACTIVE']
            chosen = (active or records)[0]
            d = dict(chosen.data)

            analysis = assess(d, pref)
            deal = score_deal(d, market_comps, pref)

            historical_only = all(l.source == 'MarketCheck' for l in (active or records)) and not MARKETCHECK_ARCHIVE_ENABLED
            risks = list(analysis.get('risks', []))
            if historical_only:
                risks.append('Historical MarketCheck snapshot; availability and price are not refreshed')

            prices = {l.data.get('price') for l in active if l.data.get('price') is not None}
            if len(prices) > 1:
                risks.append('Conflicting advertised prices across sources')
                if analysis['category'] != 'REJECTED':
                    analysis['category'] = 'MANUAL REVIEW'

            item = dict(d)
            item.update(analysis)
            item['deal_score'] = analysis['preference_score']
            item['category'] = analysis['category']
            item['reasons'] = analysis.get('reasons', [])
            item['risks'] = risks
            item['hw4'] = analysis.get('hw4', 'UNKNOWN')
            item['is_hw4'] = False # No physical verification from model-year inference
            item['historical_only'] = historical_only
            item['watched'] = v.watched
            item['first_seen'] = v.first_seen
            item['last_seen'] = v.last_seen
            item['status'] = chosen.status
            item['source_count'] = len(records)
            item['source'] = chosen.source
            item['url'] = chosen.url
            item['sources'] = [
                dict(name=l.source, url=l.url, observed=l.last_seen, status=l.status, price=l.data.get('price'))
                for l in records
            ]
            findings=investigate([dict(source=l.source,url=l.url,observed=l.last_seen,data=l.data) for l in records], evidence_by_vin.get(v.vin,[]), pref)
            item['investigation']=findings
            item['risks']=list(dict.fromkeys(item['risks']+findings['warnings']))
            if findings['purchase_readiness']=='HOLD' and item['category']!='REJECTED':
                item['category']='MANUAL REVIEW'
            result.append(item)
    return sorted(result, key=ranking_key)

@app.get('/api/state')
def state():
    rows=vehicle_rows()
    with Session() as s:
        sources=[dict(name=x.name,status=x.status,error=x.error,last_success=x.last_success,next_scan=x.next_scan,scans=x.scans,observations=x.observations,api_calls=x.request_count) for x in s.scalars(select(Source))]
        events=[dict(at=x.at,message=x.message) for x in s.scalars(select(Activity).order_by(Activity.id.desc()).limit(80))]
    scheduled={'Autohub Texas','Autos of Dallas','Discovered dealers','MarketCheck'}
    for source in sources:
        source['automatically_scheduled']=source['name'] in scheduled and (source['name']!='MarketCheck' or MARKETCHECK_ARCHIVE_ENABLED)
        source['freshness']='UNKNOWN'
        if source['last_success']:
            try:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(source['last_success'])).total_seconds()
                source['freshness']='RECENT' if age<86400 else 'STALE'
            except ValueError:pass
        source.update(scan_metrics.get(source['name'],{}))
    projected=sum(x.get('last_count',0)*3600/max(86400 if x['name']=='MarketCheck' else settings()['dealer_interval_minutes']*60, x['next_scan']-datetime.fromisoformat(x['last_success']).timestamp() if x['last_success'] else 86400) for x in sources if x['automatically_scheduled'] and x['freshness']=='RECENT' and x['status'] not in ['DISABLED','NOT CONFIGURED','UNAVAILABLE'])
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


async def perform_scan_all():
    # Route through the same budgeting, schedules and failure reporting as worker.
    for name in SOURCE_NAMES:
        with Session() as session:
            source=session.get(Source,name)
            due=source is not None and source.next_scan<=time.time()
        if due: await scan_source(name)

@app.post('/api/scan/{source_name}')
async def trigger_scan(source_name: str):
    if source_name not in SOURCE_NAMES:raise HTTPException(404,'Unknown source')
    with Session() as session:
        src=session.get(Source,source_name)
        if src and src.next_scan>time.time():raise HTTPException(429,'Source cooldown active')
    asyncio.create_task(scan_source(source_name))
    return {'status': f'Scan triggered for {source_name}'}

@app.post('/api/scan-all')
async def trigger_scan_all():
    asyncio.create_task(perform_scan_all())
    return {'status': 'Concurrent scan triggered across all sources'}

class MarketCheckKeyUpdate(BaseModel):
    api_key: str

@app.get('/api/marketcheck-key')
def get_marketcheck_key():
    key = os.getenv('MARKETCHECK_API_KEY', '')
    return {
        'configured': bool(key),
        'masked': f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ('set' if key else '')
    }

@app.post('/api/marketcheck-key')
async def set_marketcheck_key(body: MarketCheckKeyUpdate):
    global MARKETCHECK_ARCHIVE_ENABLED
    key = body.api_key.strip()
    os.environ['MARKETCHECK_API_KEY'] = key
    MARKETCHECK_ARCHIVE_ENABLED = os.getenv("MARKETCHECK_RETENTION_AUTHORIZED", "").lower() == "true"
    env_path = ROOT / '.env'
    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    new_lines = [l for l in lines if not l.startswith('MARKETCHECK_API_KEY=')]
    new_lines.append(f"MARKETCHECK_API_KEY={key}")
    new_lines = [l for l in new_lines if not l.startswith('MARKETCHECK_RETENTION_AUTHORIZED=')]
    new_lines.append("MARKETCHECK_RETENTION_AUTHORIZED=true")
    env_path.write_text('\n'.join(new_lines) + '\n')
    activity(f"MarketCheck API key updated ({key[:4]}...{key[-4:] if len(key)>8 else ''})")
    asyncio.create_task(scan_source('MarketCheck'))
    return {'status': 'Key saved and MarketCheck scan triggered'}

@app.get('/eval')
@app.get('/deals.md')
def eval_markdown():
    from fastapi.responses import PlainTextResponse
    rows = vehicle_rows()
    pref = settings()
    max_p = pref.get('max_price', 33000)
    filtered = [
        r for r in rows
        if r.get('price') and r.get('price') <= max_p
        and (r.get('year', 0) >= 2024 or r.get('is_hw4'))
        and r.get('category') != 'TRASH VIN'
    ]
    md = [f"# Tesla Deal Hunter Feed — {now()}", f"Found {len(filtered)} candidate listings requiring verification:\n"]
    for v in filtered[:50]:
        md.append(f"### {v.get('year')} Tesla {v.get('model')} {v.get('trim', '')}")
        md.append(f"- **Price**: ${v.get('price'):,}")
        p_miles = f"{v.get('mileage'):,} mi" if isinstance(v.get('mileage'), (int, float)) else "Unknown"
        md.append(f"- **Mileage**: {p_miles}")
        md.append(f"- **Deal Score**: {v.get('deal_score', 0)}/100 ({v.get('category')})")
        md.append(f"- **Hardware**: {v.get('hw4')}")
        md.append(f"- **Dealer**: {v.get('dealer')} ({v.get('distance', 'unverified')} mi)")
        md.append(f"- **Link**: {v.get('url')}\n")
    return PlainTextResponse('\n'.join(md))

app.mount('/static' ,StaticFiles(directory=ROOT/'app/static'),name='static')
@app.get('/')
def home():return FileResponse(ROOT/'app/static/index.html')


class EvidenceInput(BaseModel):
    field: str
    value: str = Field(min_length=1,max_length=2000)
    kind: str
    source: str = Field(min_length=1,max_length=500)
    url: str = Field(default='',max_length=2000)

@app.get('/api/investigate/{vin}')
def investigate_vin(vin:str):
    try:vin=normalize_vin(vin)
    except ValueError:raise HTTPException(422,'VIN format/checksum invalid')
    with Session() as session:
        records=list(session.scalars(select(Listing).where(Listing.vin==vin)))
        snapshots=list(session.scalars(select(Observation).where(Observation.vin==vin).order_by(Observation.id)))
        observations=[dict(source=x.source,url=x.url,observed=x.observed,data=x.data) for x in snapshots]
        observations += [dict(source=x.source,url=x.url,observed=x.last_seen,data=x.data) for x in records]
        claims=[dict(id=x.id,field=x.field,value=x.value,kind=x.kind,source=x.source,url=x.url,observed=x.observed) for x in session.scalars(select(Evidence).where(Evidence.vin==vin,Evidence.retracted==False).order_by(Evidence.id))]
    return dict(vin=vin,observations=observations,research_leads=[x for x in discovery_state()['leads'] if x.get('vin')==vin],**investigate(observations,claims,settings()))

@app.post('/api/investigate/{vin}/evidence')
def add_evidence(vin:str,p:EvidenceInput):
    try:vin=normalize_vin(vin)
    except ValueError:raise HTTPException(422,'VIN format/checksum invalid')
    if p.field not in FIELDS or p.kind not in KINDS:raise HTTPException(422,'Unsupported evidence field or kind')
    if p.url and not canonical_discovery_url(p.url):raise HTTPException(422,'Invalid evidence URL')
    with Session.begin() as session:
        e=Evidence(vin=vin,**p.model_dump());session.add(e);session.flush();identifier=e.id
    return {'id':identifier,'status':'Recorded as user-supplied evidence, not independently authenticated'}

@app.post('/api/investigate/{vin}/evidence/{identifier}/retract')
def retract_evidence(vin:str,identifier:int):
    with Session.begin() as session:
        e=session.get(Evidence,identifier)
        if not e or e.vin!=vin:raise HTTPException(404)
        e.retracted=True
    return {'status':'Retracted; audit record preserved'}



@app.get('/investigator')
def investigator_page():
    return FileResponse(ROOT/'app/static/investigator.html')
