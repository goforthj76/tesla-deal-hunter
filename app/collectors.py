"""Multi-source fast vehicle collector engine for Tesla Deal Hunter."""
import asyncio
import json
import re
import os
import logging
import time
from html import unescape
from urllib.parse import urlparse, quote
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

from .analysis import normalize_vin, number

log = logging.getLogger('hunter')

AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
AGENT_POLITE = 'TeslaDealHunter/0.2 (personal used-vehicle research; low-frequency)'
AUTOHUB = 'https://www.autohubtexas.com'

MODEL_CODE_MAP = {
    'm3': 'Model 3',
    'my': 'Model Y',
    'ms': 'Model S',
    'mx': 'Model X',
    'ct': 'Cybertruck'
}

# -------------------------------------------------------------
# 1. TESLA.COM OFFICIAL USED INVENTORY (Primary API)
# -------------------------------------------------------------
async def tesla_used(settings: dict, emit) -> tuple[list[dict], bool]:
    zip_code = str(settings.get('zip_code', '75022'))
    radius = int(settings.get('radius', 100))
    max_price = float(settings.get('max_price', 32000))
    all_rows = []
    complete = True

    async def fetch_model(model_code: str):
        model_name = MODEL_CODE_MAP.get(model_code, 'Tesla')
        offset = 0
        batch_size = 50
        model_rows = []

        headers = {
            'User-Agent': AGENT,
            'Accept': 'application/json, text/plain, */*',
            'Referer': f'https://www.tesla.com/inventory/used/{model_code}',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        async with httpx.AsyncClient(timeout=25, headers=headers) as client:
            while offset < 200:
                query_payload = {
                    "query": {
                        "model": model_code,
                        "condition": "used",
                        "options": {},
                        "arrangeby": "Price",
                        "order": "asc",
                        "market": "US",
                        "language": "en",
                        "super_region": "north america",
                        "zip": zip_code,
                        "range": radius,
                        "outsideSearch": False
                    },
                    "offset": offset,
                    "count": batch_size,
                    "outsideOffset": 0
                }
                
                query_encoded = quote(json.dumps(query_payload))
                url = f"https://www.tesla.com/inventory/api/v4/inventory-results?query={query_encoded}"
                
                try:
                    if cffi_requests:
                        loop = asyncio.get_event_loop()
                        resp = await loop.run_in_executor(
                            None,
                            lambda: cffi_requests.get(url, headers=headers, impersonate="chrome120", timeout=20)
                        )
                        if resp.status_code != 200:
                            break
                        data = resp.json()
                    else:
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            break
                        data = resp.json()

                    results = data.get('results') or []
                    if not results:
                        break

                    for car in results:
                        try:
                            vin = car.get('VIN')
                            if not vin:
                                continue
                            try:
                                vin = normalize_vin(vin)
                            except Exception:
                                pass
                            
                            p = number(car.get('Price') or car.get('PurchasePrice'))
                            if p and p > max_price:
                                continue

                            trim = car.get('TrimName') or (car.get('TRIM')[0] if car.get('TRIM') else '')
                            drivetrain = 'AWD' if 'AWD' in str(car.get('TRIM', [])) else 'RWD'
                            metro = car.get('MetroName') or car.get('City') or 'Dallas Area'
                            
                            row = {
                                'vin': vin,
                                'year': car.get('Year'),
                                'model': model_name,
                                'trim': trim,
                                'drivetrain': drivetrain,
                                'price': p,
                                'mileage': number(car.get('Odometer')),
                                'dealer': f'Tesla {metro}',
                                'address': f"{car.get('City', '')}, {car.get('StateProvince', '')}",
                                'latitude': car.get('Latitude'),
                                'longitude': car.get('Longitude'),
                                'description': f"Official Tesla Used Inventory ({car.get('TitleStatus', 'Clean')}). Options: {', '.join(car.get('OptionCodeList', []))}",
                                'url': f"https://www.tesla.com/used/{vin}",
                                'image': f"https://digitalassets.tesla.com/compositor/?model={model_code}&view=STUD_FRONT34&size=800",
                                'availability': 'Available at Tesla Hub',
                                'source': 'Tesla.com'
                            }
                            model_rows.append(row)
                        except Exception as e:
                            log.debug(f"Error parsing Tesla listing: {e}")

                    total_found = data.get('total_matches_found', 0)
                    offset += batch_size
                    if offset >= total_found or len(results) < batch_size:
                        break
                    await asyncio.sleep(0.5)

                except Exception as e:
                    emit(f"Tesla.com ({model_name}) error: {e}")
                    break
        return model_rows

    emit(f"Tesla.com: querying official used inventory for zip {zip_code}...")
    tasks = [fetch_model(m) for m in ['m3', 'my', 'ms', 'mx']]
    batches = await asyncio.gather(*tasks, return_exceptions=True)
    for b in batches:
        if isinstance(b, list):
            all_rows.extend(b)

    emit(f"Tesla.com: retrieved {len(all_rows)} live listings")
    return all_rows, complete

# -------------------------------------------------------------
# 2. CARS.COM
# -------------------------------------------------------------
async def cars_com(settings: dict, emit) -> tuple[list[dict], bool]:
    zip_code = str(settings.get('zip_code', '75022'))
    radius = int(settings.get('radius', 100))
    max_price = int(settings.get('max_price', 32000))
    rows = []

    url = (
        f"https://www.cars.com/shopping/results/"
        f"?makes[]=tesla&stock_type=used&zip={zip_code}&maximum_distance={radius}"
        f"&list_price_max={max_price}&sort=list_price&page_size=50"
    )

    headers = {
        'User-Agent': AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        emit("Cars.com: searching listings...")
        html_text = ""
        if cffi_requests:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: cffi_requests.get(url, headers=headers, impersonate="chrome120", timeout=20)
            )
            if resp.status_code == 200:
                html_text = resp.text
        else:
            async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    html_text = resp.text

        if html_text:
            soup = BeautifulSoup(html_text, 'html.parser')
            script = soup.find('script', id='__NEXT_DATA__')
            if script and script.string:
                data = json.loads(script.string)
                listings = data.get('props', {}).get('pageProps', {}).get('initialState', {}).get('search', {}).get('listings', [])
                for car in listings:
                    vin = car.get('vin')
                    if not vin:
                        continue
                    try:
                        vin = normalize_vin(vin)
                    except Exception:
                        pass
                    
                    price = number(car.get('pricing', {}).get('primary'))
                    miles = number(car.get('mileage'))
                    title = car.get('title', '')
                    m_year = re.search(r'(20\d\d)', title)
                    year = int(m_year[1]) if m_year else None
                    model = 'Model Y' if 'Model Y' in title else 'Model 3' if 'Model 3' in title else 'Model S' if 'Model S' in title else 'Model X' if 'Model X' in title else 'Tesla'
                    
                    dealer_name = car.get('seller', {}).get('name', 'Dealer')
                    dealer_lat = car.get('seller', {}).get('latitude')
                    dealer_lon = car.get('seller', {}).get('longitude')
                    vdp_url = f"https://www.cars.com/vehicledetail/{car.get('id')}/"

                    rows.append({
                        'vin': vin,
                        'year': year,
                        'model': model,
                        'trim': title,
                        'drivetrain': 'AWD' if 'AWD' in title.upper() else 'RWD',
                        'price': price,
                        'mileage': miles,
                        'dealer': dealer_name,
                        'address': f"{car.get('seller', {}).get('city', '')}, {car.get('seller', {}).get('state', '')}",
                        'latitude': dealer_lat,
                        'longitude': dealer_lon,
                        'description': f"Cars.com deal rating: {car.get('badges', {}).get('priceBadge', 'Standard')}",
                        'url': vdp_url,
                        'image': car.get('media', {}).get('primaryPhotoUrl'),
                        'availability': 'Active Listing',
                        'source': 'Cars.com'
                    })

        emit(f"Cars.com: extracted {len(rows)} listings")
        return rows, True
    except Exception as e:
        emit(f"Cars.com parser note: {e}")
        return rows, False

# -------------------------------------------------------------
# 3. CARGURUS
# -------------------------------------------------------------
async def cargurus(settings: dict, emit) -> tuple[list[dict], bool]:
    zip_code = str(settings.get('zip_code', '75022'))
    radius = int(settings.get('radius', 100))
    max_price = int(settings.get('max_price', 32000))
    rows = []

    url = (
        f"https://www.cargurus.com/Cars/inventorylisting/ajaxFetchSubsetInventoryListing.action"
        f"?zip={zip_code}&distance={radius}&maxPrice={max_price}"
        f"&entitySelectingHelper.selectedEntity=d2531&sortType=PRICE&sortDir=ASC"
    )

    headers = {
        'User-Agent': AGENT,
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': f'https://www.cargurus.com/Cars/l-Used-Tesla-m112?postalCode={zip_code}',
    }

    try:
        emit("CarGurus: checking listings...")
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    listings = data.get('listings', []) or data.get('subset', [])
                    for c in listings:
                        vin = c.get('vin')
                        if not vin:
                            continue
                        try:
                            vin = normalize_vin(vin)
                        except Exception:
                            pass
                        
                        price = number(c.get('price'))
                        rows.append({
                            'vin': vin,
                            'year': c.get('carYear'),
                            'model': c.get('modelName') or 'Tesla',
                            'trim': c.get('trimName', ''),
                            'drivetrain': 'AWD' if 'AWD' in str(c.get('trimName', '')).upper() else 'RWD',
                            'price': price,
                            'mileage': number(c.get('mileage')),
                            'dealer': c.get('sellerName', 'CarGurus Dealer'),
                            'address': f"{c.get('sellerCity', '')}, {c.get('sellerState', '')}",
                            'latitude': c.get('latitude'),
                            'longitude': c.get('longitude'),
                            'description': f"CarGurus deal score: {c.get('dealRating', '')} (${c.get('expectedPriceDifference', 0)} vs market)",
                            'url': f"https://www.cargurus.com/Cars/link/{c.get('id')}",
                            'image': c.get('originalPictureUrl'),
                            'availability': 'Active Listing',
                            'source': 'CarGurus'
                        })
                except Exception:
                    pass
        emit(f"CarGurus: found {len(rows)} listings")
        return rows, True
    except Exception as e:
        emit(f"CarGurus note: {e}")
        return rows, False

# -------------------------------------------------------------
# 4. CARVANA
# -------------------------------------------------------------
async def carvana_search(settings: dict, emit) -> tuple[list[dict], bool]:
    zip_code = str(settings.get('zip_code', '75022'))
    max_price = int(settings.get('max_price', 32000))
    rows = []

    url = "https://www.carvana.com/search-api/vehicles"
    payload = {
        "makes": [{"name": "Tesla"}],
        "zip5": zip_code,
        "maxPrice": max_price,
        "page": 1,
        "pageSize": 50,
        "sortBy": "LowestPrice"
    }

    headers = {
        'User-Agent': AGENT,
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

    try:
        emit("Carvana: querying catalog...")
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                for v in data.get('vehicles', []):
                    vin = v.get('vin')
                    if not vin:
                        continue
                    try:
                        vin = normalize_vin(vin)
                    except Exception:
                        pass
                    
                    rows.append({
                        'vin': vin,
                        'year': v.get('year'),
                        'model': v.get('model', 'Tesla'),
                        'trim': v.get('trim', ''),
                        'drivetrain': 'AWD' if 'AWD' in str(v.get('trim', '')).upper() else 'RWD',
                        'price': number(v.get('price')),
                        'mileage': number(v.get('mileage')),
                        'dealer': 'Carvana (Direct)',
                        'address': 'Direct delivery to Flower Mound/Plano TX',
                        'latitude': None,
                        'longitude': None,
                        'description': f"Carvana 7-day money-back guarantee. Shipping: {v.get('shippingFee', 'Free')}",
                        'url': f"https://www.carvana.com/vehicle/{v.get('vehicleId')}",
                        'image': v.get('photoUrl'),
                        'availability': 'Available for Online Purchase',
                        'source': 'Carvana'
                    })
        emit(f"Carvana: {len(rows)} vehicles found")
        return rows, True
    except Exception as e:
        emit(f"Carvana note: {e}")
        return rows, False

# -------------------------------------------------------------
# 5. AUTOHUB TEXAS
# -------------------------------------------------------------
def parse_product(html, url):
    for raw in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.S|re.I):
        try:
            product = json.loads(raw)
        except Exception:
            continue
        if product.get('@type') != 'Product': continue
        name = product.get('name','')
        match = re.search(r'(20\d\d)\s+Tesla\s+(Model\s+[3YSX])', name, re.I)
        if not match: continue
        description = unescape(product.get('description',''))
        vm = re.search(r'vin\s*:\s*([A-HJ-NPR-Z0-9]{17})', description, re.I)
        if not vm: continue
        vin = normalize_vin(vm[1])
        mm = re.search(r'(\d[\d,]*)\s*miles', description.replace(vm[0],''), re.I)
        offers = product.get('offers',{})
        images = product.get('image',[])
        image = images[0].get('contentUrl') if images and isinstance(images[0],dict) else None
        return dict(
            vin=vin,
            year=int(match[1]),
            model='Model ' + match[2].split()[-1].upper(),
            trim=name[match.end():].strip(),
            drivetrain='AWD' if 'awd' in name.lower() else 'RWD' if 'rwd' in name.lower() else None,
            price=number(offers.get('price')),
            mileage=number(mm[1]) if mm else None,
            dealer='Autohub Texas',
            address='13017 Harmon Rd Unit 209, Fort Worth, TX 76177',
            latitude=32.9644,
            longitude=-97.3114,
            description=description,
            url=url,
            image=image,
            availability=offers.get('availability','UNKNOWN'),
            source='Autohub Texas'
        )
    return None

async def autohub(emit):
    async with httpx.AsyncClient(timeout=25, headers={'User-Agent':AGENT_POLITE}, follow_redirects=False) as client:
        try:
            r = await client.get(AUTOHUB+'/robots.txt')
            robots = RobotFileParser()
            robots.parse(r.text.splitlines())
        except Exception:
            robots = None

        async def fetch(url):
            if robots and not robots.can_fetch(AGENT_POLITE, url):
                raise ValueError('Robots restriction; collection stopped')
            await asyncio.sleep(1)
            response = await client.get(url)
            return response.text

        try:
            page = await fetch(AUTOHUB+'/inventory')
            links = sorted(set(unescape(x) for x in re.findall(r'href="([^\"]*/product-page/[^\"]*)"', page)))
            links = [x for x in links if urlparse(x).netloc == 'www.autohubtexas.com' and re.search(r'tesla', x, re.I)]
            emit(f'Autohub Texas: examining {len(links)} inventory pages')
            results = []
            for url in links:
                try:
                    html = await fetch(url)
                    item = parse_product(html, url)
                    if item: results.append(item)
                except Exception as e:
                    emit(f'Autohub skip {url}: {e}')
            return results, True
        except Exception as e:
            emit(f"Autohub error: {e}")
            return [], False

# -------------------------------------------------------------
# 6. AUTOS OF DALLAS
# -------------------------------------------------------------
async def dallas(emit) -> tuple[list[dict], bool]:
    origin = 'https://www.autosofdallas.com'
    async with httpx.AsyncClient(timeout=25, headers={'User-Agent': AGENT}, follow_redirects=False) as client:
        try:
            response = await client.get('https://api.overfuel.com/api/1.0/dealers/1372/search', params={'make[]': 'Tesla'})
            if response.status_code != 200:
                return [], False
            payload = response.json()
            rows = []
            results = payload.get('results', [])
            for r in results:
                if r.get('make') != 'Tesla' or r.get('status') != 'active':
                    continue
                try:
                    vin = normalize_vin(r.get('vin'))
                except Exception:
                    vin = r.get('vin')
                
                rows.append({
                    'vin': vin,
                    'year': r.get('year'),
                    'model': r.get('model'),
                    'trim': r.get('trim', ''),
                    'drivetrain': r.get('drivetrainstandard'),
                    'price': number(r.get('price')),
                    'mileage': number(r.get('mileage')),
                    'dealer': r.get('dealer', {}).get('name', 'Autos of Dallas'),
                    'latitude': 32.9515,
                    'longitude': -96.8290,
                    'url': origin + r.get('url', ''),
                    'source': 'Autos of Dallas',
                    'description': 'Dealer inventory. Clean Carfax verified.',
                    'image': r.get('featuredphoto'),
                    'availability': 'In Stock',
                    'fees': []
                })
            emit(f'Autos of Dallas: {len(rows)} listings')
            return rows, True
        except Exception as e:
            emit(f"Autos of Dallas note: {e}")
            return [], False

# -------------------------------------------------------------
# 7. MARKETCHECK (Full Pagination Implementation)
# -------------------------------------------------------------
async def marketcheck(api_key, pref, emit, reserve_call):
    if not api_key:
        return [], False
    endpoint = 'https://marketcheck-prod.apigee.net/v2/search/car/active'
    rows = []
    async with httpx.AsyncClient(timeout=25) as client:
        for start in range(0, 350, 50):
            try:
                reserve_call()
            except Exception:
                return rows, False
            response = await client.get(endpoint, params={'api_key': api_key, 'start': start, 'rows': 50})
            if response.status_code != 200:
                return rows, False
            payload = response.json()
            valid_page = True
            for v in payload.get('listings', []):
                b = v.get('build', {})
                dealer = v.get('dealer', {})
                try:
                    vin = normalize_vin(v.get('vin'))
                except Exception:
                    valid_page = False
                    continue
                url = v.get('vdp_url', '')
                if urlparse(url).scheme not in ['https', 'http']:
                    continue
                rows.append(dict(
                    vin=vin, year=b.get('year'), model=b.get('model'), trim=b.get('trim', ''),
                    drivetrain=b.get('drivetrain'), price=number(v.get('price')),
                    mileage=number(v.get('miles')), dealer=dealer.get('name'),
                    latitude=number(dealer.get('latitude')),
                    longitude=float(dealer['longitude']) if dealer.get('longitude') else None,
                    url=url, source='MarketCheck', description='', image=None,
                    availability='API active listing; dealer availability unverified'
                ))
            if start + 50 >= payload.get('num_found', 0):
                return rows, True
            if not payload.get('listings'):
                return rows, False
            await asyncio.sleep(0.1)
    return rows, False

# -------------------------------------------------------------
# MASTER CONCURRENT SCANNER
# -------------------------------------------------------------
async def scan_all_sources(settings: dict, emit) -> dict[str, tuple[list[dict], bool]]:
    results = {}
    tasks = {
        'Tesla.com': tesla_used(settings, emit),
        'Cars.com': cars_com(settings, emit),
        'CarGurus': cargurus(settings, emit),
        'Carvana': carvana_search(settings, emit),
        'Autohub Texas': autohub(emit),
        'Autos of Dallas': dallas(emit),
    }

    task_keys = list(tasks.keys())
    outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for name, res in zip(task_keys, outcomes):
        if isinstance(res, Exception):
            emit(f"{name} scan exception: {res}")
            results[name] = ([], False)
        else:
            results[name] = res

    return results
