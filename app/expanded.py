"""Bounded discovery planning and dealer structured-data imports."""
import asyncio
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import httpx
from .analysis import normalize_vin, number
from .collectors import AGENT

SEEDS=json.loads((Path(__file__).parent/'research_seeds.json').read_text(encoding='utf-8'))
DEALERS=sorted({s['domain'].removeprefix('www.') for s in SEEDS} - {'dealeressential.com','zevoev.com'})
MARKETS=['carmax.com','cars.com','cargurus.com','autotrader.com','truecar.com','carvana.com','tesla.com']
TOWNS=['Flower Mound','Lewisville','Denton','Grapevine','Coppell','Southlake','Keller','Irving','Plano','Fort Worth','Dallas','Frisco']
CAP=750
BATCH=4
INTERVAL=4*3600

def plans():
    # Interleave town, local dealer, and national marketplace searches.
    groups=[ [{'query':f'used Tesla Model 3 Model Y for sale "{t}" "Texas"','label':t,'domains':[]} for t in TOWNS],
             [{'query':'used Tesla Model 3 Model Y inventory price Dallas Fort Worth Texas','label':d,'domains':[d]} for d in DEALERS],
             [{'query':'used Tesla Model 3 Model Y Dallas Fort Worth Texas 75022','label':d,'domains':[d]} for d in MARKETS] ]
    return [g[i] for i in range(max(map(len,groups))) for g in groups if i<len(g)]

def relevant(item):
    text=' '.join(str(item.get(k,'')) for k in ['title','content','snippet','url']).lower()
    if 'tesla' not in text:return False
    # Explicit obvious out-of-region search landing pages are not useful leads.
    return not re.search(r'\b(atlanta|georgia|north charleston|miami|palmetto bay)\b|[-/]ga(?:[/?_-]|$)',text)

def allowed(url):
    try:
        u=urlsplit(url)
        return u.scheme=='https' and u.port in (None,443) and not u.username and not u.password and u.hostname.removeprefix('www.') in DEALERS
    except (ValueError,AttributeError):return False

def objects(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from objects(child)
    elif isinstance(value,list):
        for child in value:yield from objects(child)

def structured_rows(html,url):
    rows={}
    for raw in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>',html,re.S|re.I):
        try:tree=json.loads(raw)
        except (ValueError,TypeError):continue
        for obj in objects(tree):
            vin=obj.get('vehicleIdentificationNumber') or obj.get('vin')
            if not vin:continue
            try:vin=normalize_vin(vin)
            except ValueError:continue
            name=str(obj.get('name',''));model=obj.get('model','')
            if isinstance(model,dict):model=model.get('name','')
            brand=obj.get('brand','')
            if isinstance(brand,dict):brand=brand.get('name','')
            title=' '.join([name,str(model),str(brand)])
            match=re.search(r'Model\s*([3Y])\b',title,re.I)
            if not match or not (re.search('tesla',title,re.I) or vin.startswith(('5YJ','7SA','LRW'))):continue
            year=str(obj.get('vehicleModelDate') or obj.get('modelDate') or '')
            ym=re.search(r'\b(20\d\d)\b',year+' '+name)
            if not ym:continue
            offer=obj.get('offers') or {}
            if isinstance(offer,list):offer=offer[0] if offer else {}
            if not isinstance(offer,dict):continue
            if str(offer.get('availability','')).endswith(('SoldOut','OutOfStock','Discontinued')):continue
            if offer.get('priceCurrency','USD')!='USD':continue
            mileage=obj.get('mileageFromOdometer')
            if isinstance(mileage,dict):
                unit=str(mileage.get('unitCode') or mileage.get('unitText') or '').lower()
                miles=number(mileage.get('value')) if unit in ['smi','mi','mile','miles'] else None
            else:miles=None
            seller=offer.get('seller') or obj.get('seller') or {}
            if not isinstance(seller,dict):seller={}
            geo=seller.get('geo') or {};geo=geo if isinstance(geo,dict) else {}
            def coordinate(key,limit):
                try:
                    value=float(geo[key]);return value if -limit<=value<=limit else None
                except (KeyError,ValueError,TypeError):return None
            image=obj.get('image')
            if isinstance(image,list):image=image[0] if image else None
            if isinstance(image,dict):image=image.get('url') or image.get('contentUrl')
            rows[vin]=dict(vin=vin,year=int(ym[1]),model='Model '+match[1].upper(),trim=str(obj.get('vehicleConfiguration') or model),drivetrain=None,price=number(offer.get('price')),mileage=miles,dealer=seller.get('name') or urlsplit(url).hostname,latitude=coordinate('latitude',90),longitude=coordinate('longitude',180),description=unescape(str(obj.get('description','')))[:10000],url=url,image=image if isinstance(image,str) else None,availability='Seller page; availability unverified',source='Discovered dealers')
    return list(rows.values())

async def inspect_dealers(urls,emit):
    """Only configured public dealer hosts; never follow redirects or execute JS."""
    urls=list(dict.fromkeys(u for u in urls if allowed(u)))[:24]
    sem=asyncio.Semaphore(4)
    groups={}
    for url in urls:groups.setdefault(urlsplit(url).netloc,[]).append(url)
    stats={'attempted':0,'read':0,'blocked':0,'failed':0,'without_vehicle_data':0}
    async def host_scan(host,links):
        rows=[]
        async with sem,httpx.AsyncClient(timeout=20,headers={'User-Agent':AGENT},follow_redirects=False) as client:
            try:
                r=await client.get('https://'+host+'/robots.txt')
                if r.status_code!=200:stats['blocked']+=len(links);return []
                robot=RobotFileParser();robot.parse(r.text.splitlines())
                delay=max(2,robot.crawl_delay(AGENT) or 0)
                if delay>30:stats['blocked']+=len(links);return []
                for url in links:
                    stats['attempted']+=1
                    if not robot.can_fetch(AGENT,url):stats['blocked']+=1;continue
                    await asyncio.sleep(delay)
                    try:
                        async with client.stream('GET',url) as response:
                            if response.status_code!=200:stats['failed']+=1;continue
                            if 'text/html' not in response.headers.get('content-type',''):stats['failed']+=1;continue
                            chunks=[];size=0
                            async for chunk in response.aiter_bytes():
                                size+=len(chunk)
                                if size>3_000_000:raise ValueError('Page exceeds limit')
                                chunks.append(chunk)
                        found=structured_rows(b''.join(chunks).decode('utf-8',errors='replace'),url)
                        stats['read']+=1;stats['without_vehicle_data']+=int(not found);rows.extend(found)
                    except (httpx.HTTPError,ValueError):stats['failed']+=1
            except httpx.HTTPError:stats['failed']+=len(links)
        return rows
    batches=await asyncio.gather(*(host_scan(h,links) for h,links in groups.items()))
    rows=[r for batch in batches for r in batch]
    emit(f'Dealer discovery: {stats["read"]} pages read, {len(rows)} vehicle records, {stats["blocked"]} restricted, {stats["failed"]} failed')
    return rows,stats
