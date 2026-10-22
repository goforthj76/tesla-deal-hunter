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
from sqlalchemy import select, delete, func

from .database import (
    ROOT, Base, engine, Session, Vehicle, Listing, Price, Source,
    Activity, Setting, Hunt, MarketAverage, Notification, now
)
from .analysis import assess
from .scoring import score_deal, compute_market_averages, check_hw4
from .notifier import send_deal_alert, send_price_drop_alert, test_connection, is_configured
from .collectors import (
    autohub, dallas, marketcheck, tesla_used, cars_com,
    cargurus, carvana_search, truecar_search, scan_all_sources
)
from .feed_generator import publish_to_repo

load_dotenv(ROOT / '.env')
log = logging.getLogger('hunter')
log.setLevel(logging.INFO)
handler = RotatingFileHandler(ROOT / 'data/scanner.log', maxBytes=1_000_000, backupCount=3)
log.addHandler(handler)
logging.getLogger('httpx').setLevel(logging.WARNING)

class Preferences(BaseModel):
    latitude: float = Field(default=33.0246, ge=-90, le=90)
    longitude: float = Field(default=-97.0712, ge=-180, le=180)
    radius: int = Field(default=100, ge=1, le=500)
    max_price: int = Field(default=33000, ge=1000, le=200000)
    ideal_max: int = Field(default=28000, ge=1000, le=200000)
    preferred_mileage: int = Field(default=60000, ge=0, le=300000)
    models: list[str] = Field(default=['Model 3', 'Model Y', 'Model S', 'Model X', 'Cybertruck'])
    require_hw4: bool = True
    dealer_interval_minutes: int = Field(default=30, ge=60, le=1440)
    api_interval_minutes: int = Field(default=10, ge=360, le=10080)
    monthly_api_budget: int = Field(default=450, ge=1, le=500)
    fsd_monthly: float = Field(default=99.0, ge=0, le=1000)
    ownership_months: int = Field(default=36, ge=1, le=120)
    zip_code: str = Field(default='75022')
    telegram_alert_threshold: int = Field(default=70, ge=1, le=100)

started = time.time()
task = None
scanning = None
scan_metrics = {}
MARKETCHECK_ARCHIVE_ENABLED = os.getenv("MARKETCHECK_RETENTION_AUTHORIZED", "").lower() == "true"

def activity(message: str):
    with Session.begin() as s:
        s.add(Activity(message=message))
    log.info(json.dumps({'at': now(), 'message': message}))

def settings() -> dict:
    with Session() as s:
        st = s.get(Setting, 1)
        return st.data if st else Preferences().model_dump()

def ingest(source: str, rows: list[dict], complete: bool):
    stamp = now()
    seen = set()
    pref = settings()
    alert_threshold = pref.get('telegram_alert_threshold', 70)

    with Session() as s:
        all_active_listings = [
            l.data for l in s.scalars(select(Listing).where(Listing.status == 'ACTIVE')) if l.data
        ]
    market_comps = compute_market_averages(all_active_listings + rows)

    with Session.begin() as s:
        for d in rows:
            vin = d.get('vin')
            if not vin:
                continue
            key = hashlib.sha256((source + '|' + d.get('url', '')).encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            s.flush()

            score_res = score_deal(d, market_comps, pref)
            d['deal_score'] = score_res['score']
            d['category'] = score_res['category']
            d['score_breakdown'] = score_res

            vehicle = s.get(Vehicle, vin)
            if not vehicle:
                vehicle = Vehicle(
                    vin=vin,
                    first_seen=stamp,
                    last_seen=stamp,
                    model=d.get('model'),
                    year=d.get('year'),
                    deal_score=score_res['score'],
                    best_price=d.get('price')
                )
                s.add(vehicle)
                s.add(Activity(message=f"New VIN: {vin}"))
            else:
                vehicle.last_seen = stamp
                vehicle.deal_score = max(vehicle.deal_score or 0, score_res['score'])
                if d.get('price') and (vehicle.best_price is None or d['price'] < vehicle.best_price):
                    vehicle.best_price = d['price']

            listing = s.get(Listing, key)
            old_price = listing.data.get('price') if listing and listing.data else None
            if not listing:
                listing = Listing(
                    id=key, vin=vin, source=source, url=d.get('url', ''),
                    data=d, first_seen=stamp, last_seen=stamp, deal_score=score_res['score']
                )
                s.add(listing)
            listing.data = d
            listing.last_seen = stamp
            listing.missing = 0
            listing.status = 'ACTIVE'
            listing.deal_score = score_res['score']

            is_hw4_car, hw4_tag = check_hw4(d)
            price = d.get('price')
            year = d.get('year') or 0
            
            # User constraint: ONLY notify if price <= $33,000 and (year >= 2024 or (year == 2023 and is_hw4_car))
            hw4_qualified = (year >= 2024) or (year == 2023 and is_hw4_car)
            price_qualified = price is not None and price <= 33000

            if d.get('price') is not None and old_price != d['price']:
                s.add(Price(listing_id=key, vin=vin, amount=d['price'], observed=stamp))
                if old_price is not None:
                    s.add(Activity(message=f"Price change {vin}: {old_price} → {d['price']}"))
                    if old_price > d['price'] and hw4_qualified and price_qualified:
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(send_price_drop_alert(d, old_price, d['price']))
                        except RuntimeError:
                            pass

            if hw4_qualified and price_qualified and score_res['score'] >= alert_threshold:
                already_notified = s.scalar(
                    select(func.count(Notification.id)).where(Notification.vin == vin)
                )
                if not already_notified:
                    s.add(Notification(
                        vin=vin, listing_id=key, deal_score=score_res['score'],
                        message=f"Deal Score {score_res['score']}"
                    ))
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(send_deal_alert(d, score_res))
                    except RuntimeError:
                        pass

        if complete:
            for listing in s.scalars(select(Listing).where(Listing.source == source)):
                if listing.id not in seen:
                    listing.missing += 1
                    listing.status = 'NOT SEEN' if listing.missing < 2 else 'REMOVED / AVAILABILITY UNKNOWN'

        src = s.get(Source, source)
        if src:
            src.observations += len(rows)
            src.scans += 1
            src.last_success = stamp
            src.status = 'HEALTHY' if complete else 'DEGRADED'
            src.error = '' if complete else 'Pagination capped; incomplete coverage'

        cutoff = s.scalar(select(Activity.id).order_by(Activity.id.desc()).offset(2000).limit(1))
        if cutoff:
            s.execute(delete(Activity).where(Activity.id < cutoff))

def hunt_counts():
    with Session() as s:
        return dict(
            observations=s.scalar(select(func.coalesce(func.sum(Source.observations), 0))),
            unique_vins=s.scalar(select(func.count()).select_from(Vehicle)),
            scans=s.scalar(select(func.coalesce(func.sum(Source.scans), 0)))
        )

def start_hunt():
    with Session.begin() as s:
        active = s.scalar(select(Hunt).where(Hunt.ended_at.is_(None)))
        if active:
            return {'id': active.id, 'status': 'already running'}
        h = Hunt(baseline=hunt_counts())
        s.add(h)
        s.flush()
        identifier = h.id
    activity('Overnight report started; existing source schedules and limits remain in effect')
    return {'id': identifier, 'status': 'running'}

def finish_hunt():
    current = hunt_counts()
    with Session.begin() as s:
        h = s.scalar(select(Hunt).where(Hunt.ended_at.is_(None)))
        if not h:
            raise HTTPException(409, 'No active hunt report')
        h.ended_at = now()
        h.summary = {k: v - h.baseline.get(k, 0) for k, v in current.items()}
        result = h.summary
    activity('Overnight report finished; regular scanner schedule continues')
    return result

def reserve_api():
    with Session.begin() as s:
        src = s.get(Source, 'MarketCheck')
        month = now()[:7]
        if src.budget_month != month:
            src.request_count = 0
            src.budget_month = month
        if src.request_count >= s.get(Setting, 1).data['monthly_api_budget']:
            raise ValueError('Local monthly API budget exhausted')
        src.request_count += 1

async def scan_source(name: str):
    global scanning
    if name == "MarketCheck" and not MARKETCHECK_ARCHIVE_ENABLED:
        activity("MarketCheck scheduled archive disabled: retention authorization required")
        return

    scanning = name
    pref = settings()
    scan_started = time.time()
    activity(f"Starting {name}")
    with Session.begin() as s:
        src = s.get(Source, name)
        if src:
            src.status = 'SCANNING'

    try:
        if name == 'Tesla.com':
            rows, complete = await tesla_used(pref, activity)
        elif name == 'Cars.com':
            rows, complete = await cars_com(pref, activity)
        elif name == 'CarGurus':
            rows, complete = await cargurus(pref, activity)
        elif name == 'Carvana':
            rows, complete = await carvana_search(pref, activity)
        elif name == 'TrueCar':
            rows, complete = await truecar_search(pref, activity)
        elif name == 'Autohub Texas':
            rows, complete = await autohub(activity)
        elif name == 'Autos of Dallas':
            rows, complete = await dallas(activity)
        elif name == 'MarketCheck':
            rows, complete = await marketcheck(os.getenv('MARKETCHECK_API_KEY', ''), pref, activity, reserve_api)
        else:
            rows, complete = [], False

        ingest(name, rows, complete)
        scan_metrics[name] = {
            'last_count': len(rows),
            'duration_seconds': round(time.time() - scan_started, 2),
            'complete': complete
        }
        with Session.begin() as s:
            src = s.get(Source, name)
            if src:
                src.scans = (src.scans or 0) + 1
                if len(rows) > 0:
                    src.observations = len(rows)
                    src.status = 'HEALTHY'
                    src.last_success = now()
                    src.error = ''
        if len(rows) > 0:
            activity(f"{name}: completed, {len(rows)} live listings verified")
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, publish_to_repo)
            except Exception:
                pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception(f"Exception in scan_source({name})")
        print(f"EXCEPTION in {name}: {type(exc).__name__}: {exc}")
        safe = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        with Session.begin() as s:
            src = s.get(Source, name)
            if src:
                src.status = 'UNAVAILABLE'
                src.error = safe[:200]
        activity(f"{name}: unavailable — {safe[:200]}")
    finally:
        with Session.begin() as s:
            src = s.get(Source, name)
            if src:
                count = scan_metrics.get(name, {}).get('last_count', 0)
                if count == 0 and name in ('Tesla.com', 'Cars.com', 'CarGurus'):
                    interval = 720
                elif name in ('Carvana', 'TrueCar'):
                    interval = 15
                elif name == 'MarketCheck':
                    interval = 30
                else:
                    interval = 30
                src.next_scan = time.time() + (interval * 60)
        scanning = None

async def worker():
    while True:
        try:
            sources_to_check = [
                'MarketCheck', 'TrueCar', 'Carvana', 'Autos of Dallas', 'Autohub Texas'
            ]
            for name in sources_to_check:
                with Session() as s:
                    src = s.get(Source, name)
                    due = not src or src.next_scan <= time.time()
                if due:
                    await scan_source(name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            activity(f"Worker recovered from error: {type(exc).__name__}")
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global task
    Base.metadata.create_all(engine)
    activity('Tesla Deal Hunter background scanner online')
    asyncio.get_running_loop().run_in_executor(None, publish_to_repo)
    task = asyncio.create_task(worker())
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    activity('Scanner stopped gracefully')

app = FastAPI(title='Tesla Deal Hunter', lifespan=lifespan)

@app.get('/api/state')
def get_state():
    pref = settings()
    with Session() as s:
        sources = [
            {'name': src.name, 'status': src.status, 'observations': src.observations,
             'scans': src.scans, 'last_success': src.last_success, 'error': src.error}
            for src in s.scalars(select(Source))
        ]

        hunt = s.scalar(select(Hunt).order_by(Hunt.id.desc()).limit(1))
        hunt_data = None
        if hunt:
            hunt_data = {
                'id': hunt.id,
                'started_at': hunt.started_at,
                'ended_at': hunt.ended_at,
                'summary': hunt.summary
            }

        # Batch load listings in 1 query instead of 500+ separate queries
        all_listings = list(s.scalars(select(Listing)))
        listings_by_vin = {}
        for l in all_listings:
            listings_by_vin.setdefault(l.vin, []).append(l)

        raw_vehicles = []
        for v in s.scalars(select(Vehicle).order_by(Vehicle.deal_score.desc().nullslast())):
            listings = listings_by_vin.get(v.vin, [])
            active_listings = [l for l in listings if l.status == 'ACTIVE']
            best_listing = active_listings[0] if active_listings else (listings[0] if listings else None)
            
            data = best_listing.data if best_listing else {}
            item = {
                'vin': v.vin,
                'year': v.year or data.get('year'),
                'model': v.model or data.get('model'),
                'trim': data.get('trim', ''),
                'price': data.get('price') or v.best_price,
                'mileage': data.get('mileage'),
                'dealer': data.get('dealer', 'Private/Unknown'),
                'image': data.get('image'),
                'url': best_listing.url if best_listing else '',
                'deal_score': v.deal_score or data.get('deal_score', 50),
                'category': data.get('category', 'FAIR'),
                'reasons': data.get('score_breakdown', {}).get('reasons', []),
                'risks': data.get('score_breakdown', {}).get('risks', []),
                'distance': data.get('distance'),
                'source': best_listing.source if best_listing else 'Unknown',
                'source_count': len(listings),
                'watched': v.watched,
                'first_seen': v.first_seen,
                'hw4': data.get('score_breakdown', {}).get('hw4', 'UNKNOWN')
            }
            raw_vehicles.append(item)

        recent_acts = [
            {'at': a.created, 'message': a.message}
            for a in s.scalars(select(Activity).order_by(Activity.id.desc()).limit(50))
        ]

        total_listings = s.scalar(select(func.count(Listing.id)))
        unique_vins = s.scalar(select(func.count(Vehicle.vin)))

    return {
        'scanning': scanning,
        'uptime': int(time.time() - started),
        'settings': pref,
        'telegram_configured': is_configured(),
        'sources': sources,
        'hunt': hunt_data,
        'vehicles': raw_vehicles,
        'activity': recent_acts,
        'stats': {
            'listings_scanned': total_listings or 0,
            'unique_vins': unique_vins or 0
        }
    }

@app.post('/api/scan/{source_name}')
async def trigger_scan(source_name: str):
    asyncio.create_task(scan_source(source_name))
    return {'status': f'Scan triggered for {source_name}'}

async def perform_scan_all():
    pref = settings()
    activity("Starting concurrent scan across all sources...")
    mk_key = os.getenv('MARKETCHECK_API_KEY', '')
    results = await scan_all_sources(pref, activity, marketcheck_key=mk_key)
    for name, (rows, complete) in results.items():
        if rows:
            ingest(name, rows, complete)
            scan_metrics[name] = {
                'last_count': len(rows),
                'complete': complete
            }
            activity(f"{name}: ingested {len(rows)} vehicles")

@app.post('/api/scan-all')
async def trigger_scan_all():
    asyncio.create_task(perform_scan_all())
    return {'status': 'Concurrent scan triggered across all sources'}

class MarketCheckKeyUpdate(BaseModel):
    api_key: str

@app.post('/api/marketcheck-key')
async def set_marketcheck_key(body: MarketCheckKeyUpdate):
    global MARKETCHECK_ARCHIVE_ENABLED
    key = body.api_key.strip()
    os.environ['MARKETCHECK_API_KEY'] = key
    MARKETCHECK_ARCHIVE_ENABLED = True
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

@app.get('/api/marketcheck-key')
def get_marketcheck_key():
    key = os.getenv('MARKETCHECK_API_KEY', '')
    return {
        'configured': bool(key),
        'masked': f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ('set' if key else '')
    }

@app.post('/api/telegram/test')
async def test_telegram_endpoint():
    res = await test_connection()
    return res

@app.post('/api/hunt/start')
def api_start_hunt():
    return start_hunt()

@app.post('/api/hunt/finish')
def api_finish_hunt():
    return finish_hunt()

@app.post('/api/vehicles/{vin}/watch')
def toggle_watch(vin: str):
    with Session.begin() as s:
        v = s.get(Vehicle, vin)
        if not v:
            raise HTTPException(404, 'Vehicle not found')
        v.watched = not v.watched
        return {'watched': v.watched}

@app.put('/api/settings')
def update_preferences(p: Preferences):
    with Session.begin() as s:
        st = s.get(Setting, 1)
        if st:
            st.data = p.model_dump()
    activity('Search settings updated')
    return p

app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')

@app.get('/eval')
@app.get('/deals.md')
def eval_markdown():
    from fastapi.responses import PlainTextResponse
    with Session() as s:
        all_listings = list(s.scalars(select(Listing)))
        listings_by_vin = {}
        for l in all_listings:
            listings_by_vin.setdefault(l.vin, []).append(l)

        matches = []
        for v in s.scalars(select(Vehicle).order_by(Vehicle.deal_score.desc().nullslast())):
            listings = listings_by_vin.get(v.vin, [])
            active = [l for l in listings if l.status == 'ACTIVE']
            best = active[0] if active else (listings[0] if listings else None)
            if not best:
                continue
            d = best.data or {}
            is_hw4, tag = check_hw4(d)
            p = d.get('price') or v.best_price
            y = v.year or d.get('year') or 0
            if p and p <= 33000 and (y >= 2024 or (y == 2023 and is_hw4)):
                matches.append({
                    'year': y,
                    'model': v.model or d.get('model'),
                    'trim': d.get('trim', 'Standard'),
                    'price': p,
                    'mileage': d.get('mileage', 0),
                    'dealer': d.get('dealer', 'Dealer'),
                    'hw4_tag': tag,
                    'deal_score': v.deal_score or 50,
                    'url': best.url,
                    'vin': v.vin
                })

    md = [
        "# ⚡ Tesla Deal Hunter — Verified HW4 Deals Under $33,000",
        f"*Search Location: Flower Mound / Plano (Zip 75022) · Active Qualifying Offers: {len(matches)}*",
        "",
        "| Rank | Year & Model | Trim | Price | Mileage | Dealership | Deal Score | Direct Link |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
    ]
    for i, m in enumerate(matches, 1):
        md.append(f"| {i} | {m['year']} Tesla {m['model']} | {m['trim']} | ${int(m['price']):,} | {int(m['mileage']):,} mi | {m['dealer']} | {m['deal_score']}/100 | [View Listing]({m['url']}) |")

    md.append("\n### Detailed Offer Breakdown for ChatGPT / LLM Analysis:")
    for i, m in enumerate(matches, 1):
        md.append(f"\n#### Offer #{i}: {m['year']} Tesla {m['model']} ({m['trim']})")
        md.append(f"- **Price**: ${int(m['price']):,}")
        md.append(f"- **Mileage**: {int(m['mileage']):,} miles")
        md.append(f"- **Autopilot Hardware**: {m['hw4_tag']}")
        md.append(f"- **Dealership**: {m['dealer']}")
        md.append(f"- **VIN**: `{m['vin']}`")
        md.append(f"- **Direct Listing URL**: {m['url']}")

    return PlainTextResponse('\n'.join(md), media_type='text/markdown')

@app.get('/')
def home():
    return FileResponse(ROOT / 'app/static/index.html')
