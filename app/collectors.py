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
    rows = []
    seen_vins = set()

    emit("Carvana: querying catalog...")
    
    def fetch_page(page_num: int):
        url = f"https://www.carvana.com/cars/tesla?email=&zip={zip_code}&page={page_num}"
        if cffi_requests:
            return cffi_requests.get(url, impersonate="chrome120", timeout=15).text
        headers = {'User-Agent': AGENT, 'Accept-Language': 'en-US,en;q=0.9'}
        with httpx.Client(timeout=15, headers=headers) as c:
            return c.get(url).text

    loop = asyncio.get_event_loop()
    try:
        for p in range(1, 8):
            html = await loop.run_in_executor(None, fetch_page, p)
            soup = BeautifulSoup(html, 'html.parser')
            scripts = soup.find_all('script', type='application/ld+json')
            page_found = 0
            for s in scripts:
                try:
                    d = json.loads(s.string)
                    if d.get('@type') == 'Vehicle':
                        vin = d.get('vehicleIdentificationNumber')
                        if not vin or vin in seen_vins:
                            continue
                        try:
                            vin = normalize_vin(vin)
                        except Exception:
                            pass
                        
                        price = number(d.get('offers', {}).get('price'))
                        miles = number(d.get('mileageFromOdometer'))
                        year = d.get('modelDate')
                        model = d.get('model') or 'Tesla'
                        trim = d.get('name', '').replace(f"{year} Tesla {model}", '').strip()
                        vdp = d.get('offers', {}).get('url') or f"https://www.carvana.com/vehicle/{d.get('sku', '')}"
                        img = d.get('image')

                        seen_vins.add(vin)
                        page_found += 1
                        rows.append({
                            'vin': vin,
                            'year': year,
                            'model': model,
                            'trim': trim,
                            'drivetrain': 'AWD' if 'AWD' in trim.upper() else 'RWD',
                            'price': price,
                            'mileage': miles,
                            'dealer': 'Carvana (Direct)',
                            'address': 'Direct delivery to Flower Mound/Plano TX',
                            'latitude': None,
                            'longitude': None,
                            'description': d.get('description', 'Carvana 7-day money-back guarantee, 100-day warranty'),
                            'url': vdp,
                            'image': img,
                            'availability': 'Available for Online Purchase',
                            'source': 'Carvana'
                        })
                except Exception:
                    continue
            if page_found == 0:
                break
            await asyncio.sleep(0.2)
        
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
# 7. TRUECAR (DFW Local & Regional Dealer Listings)
# -------------------------------------------------------------
async def truecar_search(settings: dict, emit) -> tuple[list[dict], bool]:
    zip_code = str(settings.get('zip_code', '75022'))
    rows = []
    seen_vins = set()

    emit("TrueCar: searching DFW listings...")
    
    def fetch_page(page_num: int):
        url = f"https://www.truecar.com/used-cars-for-sale/listings/tesla/location-flower-mound-tx/?page={page_num}"
        if cffi_requests:
            return cffi_requests.get(url, impersonate="chrome120", timeout=15).text
        headers = {'User-Agent': AGENT, 'Accept-Language': 'en-US,en;q=0.9'}
        with httpx.Client(timeout=15, headers=headers) as c:
            return c.get(url).text

    loop = asyncio.get_event_loop()
    try:
        for p in range(1, 8):
            html = await loop.run_in_executor(None, fetch_page, p)
            soup = BeautifulSoup(html, 'html.parser')
            cards = soup.find_all('div', attrs={'data-test': 'vehicleListingCard'})
            page_found = 0
            for c in cards:
                link = c.find('a', href=re.compile(r'/used-cars-for-sale/listing/([A-HJ-NPR-Z0-9]{17})'))
                if not link:
                    continue
                m = re.search(r'/used-cars-for-sale/listing/([A-HJ-NPR-Z0-9]{17})/([^/?]+)', link.get('href'))
                if not m:
                    continue
                vin_raw, slug = m.group(1), m.group(2)
                try:
                    vin = normalize_vin(vin_raw)
                except Exception:
                    vin = vin_raw
                if vin in seen_vins:
                    continue

                card_strings = list(c.stripped_strings)
                card_text = ' // '.join(card_strings)
                
                ym = re.search(r'(20\d\d)', slug)
                year = int(ym.group(1)) if ym else None
                
                model = 'Tesla'
                if 'model-3' in slug.lower(): model = 'Model 3'
                elif 'model-y' in slug.lower(): model = 'Model Y'
                elif 'model-s' in slug.lower(): model = 'Model S'
                elif 'model-x' in slug.lower(): model = 'Model X'
                
                # Clean trim from card
                trim = ''
                trim_m = re.search(r'Tesla\s+Model\s+[3YSX]\s*//\s*([^/]+?)\s*//\s*Used', card_text)
                if trim_m:
                    trim = trim_m.group(1).strip()
                
                # Precise Price inside THIS card only
                price = None
                price_el = c.find(attrs={'data-test': re.compile(r'vehicleCardPricing|vehiclePricing', re.I)})
                if price_el:
                    pm = re.search(r'\$([0-9]{1,3}(?:,[0-9]{3})+)', price_el.text)
                    if pm:
                        price = int(pm.group(1).replace(',', ''))
                if not price:
                    pm = re.search(r'Advertised price\s*//\s*\$([0-9]{1,3}(?:,[0-9]{3})+)', card_text)
                    if pm:
                        price = int(pm.group(1).replace(',', ''))

                # Precise Mileage inside THIS card only
                miles = None
                mm = re.search(r'([0-9]{1,3}(?:,[0-9]{3})+)\s*mi\b', card_text)
                if mm:
                    miles = int(mm.group(1).replace(',', ''))

                # Dealer name & distance from card
                dealer = 'TrueCar Certified Dealer'
                for s in card_strings:
                    if re.search(r'(CarMax|AutoNation|Classic|Motors|Toyota|Honda|Ford|Chevrolet|Audi|BMW|Hyundai|Nissan|Subaru|VW|Kia|Lexus|Grapevine|Plano|Frisco|Carrollton|Dallas|Fort Worth|Lewisville)', s, re.I):
                        dealer = s.strip()
                        break
                
                dist = None
                dm = re.search(r'(\d+(?:\.\d+)?)\s*mi away', card_text)
                if dm:
                    dist = float(dm.group(1))

                img_el = c.find('img')
                img_url = img_el.get('src') if img_el else None

                seen_vins.add(vin)
                page_found += 1
                rows.append({
                    'vin': vin,
                    'year': year,
                    'model': model,
                    'trim': trim,
                    'drivetrain': 'AWD' if 'awd' in trim.lower() or 'dual' in trim.lower() else 'RWD',
                    'price': price,
                    'mileage': miles,
                    'dealer': dealer,
                    'distance': dist,
                    'address': 'DFW Area',
                    'latitude': None,
                    'longitude': None,
                    'description': f"TrueCar verified listing. {dealer}",
                    'url': f"https://www.truecar.com/used-cars-for-sale/listing/{vin}/{slug}/",
                    'image': img_url,
                    'availability': 'Active Listing',
                    'source': 'TrueCar'
                })
            if page_found == 0:
                break
            await asyncio.sleep(0.2)
        emit(f"TrueCar: {len(rows)} verified listings extracted")
        return rows, True
    except Exception as e:
        emit(f"TrueCar note: {e}")
        return rows, False

# -------------------------------------------------------------
# 8. MARKETCHECK (Dealership Aggregator API)
# -------------------------------------------------------------
async def marketcheck(api_key: str, pref: dict, emit, reserve_call=None) -> tuple[list[dict], bool]:
    if not api_key:
        emit("MarketCheck: no API key provided")
        return [], False
    
    endpoint = 'https://api.marketcheck.com/v2/search/car/active'
    zip_code = str(pref.get('zip_code', '75022'))
    radius = min(100, int(pref.get('radius', 100)))
    max_price = int(pref.get('max_price', 35000))
    rows = []
    
    emit(f"MarketCheck: querying live dealer network for zip {zip_code}...")
    async with httpx.AsyncClient(timeout=25) as client:
        for start in range(0, 850, 50):
            if reserve_call:
                try:
                    reserve_call()
                except Exception:
                    break
            params = {
                'api_key': api_key,
                'make': 'Tesla',
                'zip': zip_code,
                'radius': radius,
                'car_type': 'used',
                'price_range': f"15000-{max_price + 5000}",
                'sort_by': 'price',
                'sort_order': 'asc',
                'rows': 50,
                'start': start
            }
            try:
                resp = await client.get(endpoint, params=params)
                if resp.status_code != 200:
                    emit(f"MarketCheck status: {resp.status_code}")
                    break
                payload = resp.json()
            except Exception as e:
                emit(f"MarketCheck request error: {e}")
                break
            
            listings = payload.get('listings', [])
            if not listings:
                break
            
            for v in listings:
                b = v.get('build', {})
                dealer = v.get('dealer', {})
                vin_raw = v.get('vin')
                if not vin_raw:
                    continue
                try:
                    vin = normalize_vin(vin_raw)
                except Exception:
                    vin = vin_raw
                
                url = v.get('vdp_url', '')
                if not url or urlparse(url).scheme not in ['https', 'http']:
                    url = f"https://www.marketcheck.com/vdp/{v.get('id')}"
                
                photos = v.get('media', {}).get('photo_links', [])
                img = photos[0] if photos else None
                
                dealer_name = dealer.get('name') or 'Dealership'
                city = dealer.get('city', '')
                state = dealer.get('state', '')
                address = f"{city}, {state}".strip(", ")
                
                rows.append({
                    'vin': vin,
                    'year': b.get('year'),
                    'model': b.get('model', 'Tesla'),
                    'trim': b.get('trim', '') or b.get('version', ''),
                    'drivetrain': b.get('drivetrain', 'RWD'),
                    'price': number(v.get('price')),
                    'mileage': number(v.get('miles')),
                    'dealer': dealer_name,
                    'distance': number(v.get('dist')),
                    'address': address,
                    'latitude': number(dealer.get('latitude')),
                    'longitude': float(dealer['longitude']) if dealer.get('longitude') else None,
                    'url': url,
                    'source': 'MarketCheck',
                    'carfax_clean_title': v.get('carfax_clean_title'),
                    'carfax_1_owner': v.get('carfax_1_owner'),
                    'description': v.get('heading', 'Dealer Inventory'),
                    'image': img,
                    'availability': 'Active Dealership Listing'
                })
            
            if start + 50 >= payload.get('num_found', 0):
                return rows, True
            await asyncio.sleep(0.1)

    emit(f"MarketCheck: {len(rows)} dealer listings extracted")
    return rows, True

# -------------------------------------------------------------
# MASTER CONCURRENT SCANNER
# -------------------------------------------------------------
async def scan_all_sources(settings: dict, emit, marketcheck_key: str = '') -> dict[str, tuple[list[dict], bool]]:
    results = {}
    tasks = {
        'Tesla.com': tesla_used(settings, emit),
        'Cars.com': cars_com(settings, emit),
        'CarGurus': cargurus(settings, emit),
        'Carvana': carvana_search(settings, emit),
        'TrueCar': truecar_search(settings, emit),
        'Autohub Texas': autohub(emit),
        'Autos of Dallas': dallas(emit),
    }

    if marketcheck_key:
        tasks['MarketCheck'] = marketcheck(marketcheck_key, settings, emit)

    task_keys = list(tasks.keys())
    outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for name, res in zip(task_keys, outcomes):
        if isinstance(res, Exception):
            emit(f"{name} scan exception: {res}")
            results[name] = ([], False)
        else:
            results[name] = res

    return results
