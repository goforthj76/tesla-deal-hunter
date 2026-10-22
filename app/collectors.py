"""Isolated adapters. Never execute website JavaScript or bypass blocked requests."""
import asyncio
import json
import re
from html import unescape
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import httpx
from .analysis import normalize_vin, number

AGENT = 'TeslaDealHunter/0.1 (personal used-vehicle research; low-frequency)'
AUTOHUB = 'https://www.autohubtexas.com'

def parse_product(html, url):
    for raw in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.S|re.I):
        product = json.loads(raw)
        if product.get('@type') != 'Product': continue
        name = product.get('name','')
        match = re.search(r'(20\d\d)\s+Tesla\s+(Model\s+[3Y])', name, re.I)
        if not match: continue
        description = unescape(product.get('description',''))
        vm = re.search(r'vin\s*:\s*([A-HJ-NPR-Z0-9]{17})', description, re.I)
        if not vm: raise ValueError('Product missing VIN')
        vin = normalize_vin(vm[1])
        # Wix concatenates the VIN and mileage without a space. Remove VIN first.
        mm = re.search(r'(\d[\d,]*)\s*miles',description.replace(vm[0],''),re.I)
        offers = product.get('offers',{})
        images = product.get('image',[])
        image = images[0].get('contentUrl') if images and isinstance(images[0],dict) else None
        return dict(vin=vin,year=int(match[1]),model='Model '+match[2][-1].upper(),trim=name[match.end():].strip(),drivetrain='AWD' if 'awd' in name.lower() else 'RWD' if 'rwd' in name.lower() else None,price=number(offers.get('price')),mileage=number(mm[1]) if mm else None,dealer='Autohub Texas',address='13017 Harmon Rd Unit 209, Fort Worth, TX 76177',latitude=None,longitude=None,description=description,url=url,image=image,availability=offers.get('availability','UNKNOWN'),source='Autohub Texas')
    return None

async def autohub(emit):
    async with httpx.AsyncClient(timeout=25,headers={'User-Agent':AGENT},follow_redirects=False) as client:
        r = await client.get(AUTOHUB+'/robots.txt')
        r.raise_for_status()
        robots = RobotFileParser(); robots.parse(r.text.splitlines())
        async def fetch(url):
            if not robots.can_fetch(AGENT,url): raise ValueError('Robots restriction; collection stopped')
            await asyncio.sleep(max(2, robots.crawl_delay(AGENT) or 0))
            response = await client.get(url)
            response.raise_for_status()
            if 'text/html' not in response.headers.get('content-type',''): raise ValueError('Unexpected response type')
            return response.text
        page = await fetch(AUTOHUB+'/inventory')
        links = sorted(set(unescape(x) for x in re.findall(r'href="([^\"]*/product-page/[^\"]*)"',page)))
        links = [x for x in links if urlparse(x).netloc == 'www.autohubtexas.com' and re.search(r'tesla-model-(3|y)-',x)]
        if not links: raise ValueError('No expected product links; source layout requires review')
        if len(links)>40: raise ValueError('Unexpected inventory size; bounded collector stopped')
        rows=[]
        for url in links:
            emit('Reading dealer listing: '+url.rsplit('/',1)[-1])
            row = parse_product(await fetch(url),url)
            if row is None: raise ValueError('Unrecognized product format; snapshot incomplete')
            if row['availability'].endswith('/InStock'): rows.append(row)
        return rows, True

async def marketcheck(key, settings, emit, reserve):
    rows=[]
    async with httpx.AsyncClient(timeout=30,headers={'User-Agent':AGENT}) as client:
        for start in range(0,1500,50):
            reserve()
            r=await client.get('https://api.marketcheck.com/v2/search/car/active',params={'api_key':key,'append_api_key':'false','make':'Tesla','model':','.join(settings['models']),'car_type':'used','latitude':settings['latitude'],'longitude':settings['longitude'],'radius':settings['radius'],'price_range':f"0-{settings['max_price']}",'rows':50,'start':start})
            # Do not propagate request URLs containing credentials into logs.
            if r.status_code != 200:
                if rows:
                    emit('Inventory pagination stopped: HTTP '+str(r.status_code)+'; partial results retained')
                    return rows, False
                raise ValueError('Inventory API HTTP '+str(r.status_code))
            payload=r.json()
            if not isinstance(payload.get('listings'),list): raise ValueError('Unexpected inventory API schema')
            valid_page = True
            for v in payload['listings']:
                b=v.get('build') or {}; dealer=v.get('dealer') or {}
                try: vin=normalize_vin(v.get('vin'))
                except ValueError:
                    valid_page = False
                    continue
                url=v.get('vdp_url','')
                if urlparse(url).scheme not in ['https','http']: continue
                rows.append(dict(vin=vin,year=b.get('year'),model=b.get('model'),trim=b.get('trim',''),drivetrain=b.get('drivetrain'),price=number(v.get('price')),mileage=number(v.get('miles')),dealer=dealer.get('name'),latitude=number(dealer.get('latitude')),longitude=float(dealer['longitude']) if dealer.get('longitude') else None,url=url,source='MarketCheck',description='',image=None,availability='API active listing; dealer availability unverified'))
            emit(f'Inventory API page {start//50+1}: {len(rows)} valid records')
            if not valid_page: emit('Some records had invalid VINs; removal detection disabled')
            if start == 0: all_valid = True
            all_valid = all_valid and valid_page
            if start+50>=payload.get('num_found',0): return rows,all_valid
            if not payload['listings']: return rows,False
            await asyncio.sleep(2)
    return rows,False


async def dallas(emit):
    """Dealer-published Overfuel inventory API, documented by inventory.md."""
    origin='https://www.autosofdallas.com'
    async with httpx.AsyncClient(timeout=25,headers={'User-Agent':AGENT},follow_redirects=False) as client:
        robots_response=await client.get(origin+'/robots.txt')
        robots_response.raise_for_status()
        robots=RobotFileParser(); robots.parse(robots_response.text.splitlines())
        if not robots.can_fetch(AGENT,origin+'/inventory.md'):
            raise ValueError('Dealer robots rules require review')
        response=await client.get('https://api.overfuel.com/api/1.0/dealers/1372/search',params={'make[]':'Tesla'})
        response.raise_for_status(); payload=response.json()
        if not isinstance(payload.get('results'),list): raise ValueError('Dealer API schema changed')
        rows=[]; complete=len(payload['results'])>=payload.get('meta',{}).get('total',float('inf'))
        for r in payload['results']:
            if r.get('make')!='Tesla' or r.get('model') not in ['Model Y','Model 3'] or r.get('status')!='active': continue
            vin=normalize_vin(r.get('vin'))
            if not r.get('url','').startswith('/inventory/'): raise ValueError('Unexpected dealer listing URL')
            fees=[{'name':f.get('title'),'amount':f.get('amount')} for f in r.get('discounts',[]) if f.get('discounttype')=='increase']
            rows.append(dict(vin=vin,year=r.get('year'),model=r['model'],trim=r.get('trim',''),drivetrain=r.get('drivetrainstandard'),price=number(r.get('price')),mileage=number(r.get('mileage')),dealer=r.get('dealer',{}).get('name','Autos of Dallas'),latitude=None,longitude=None,url=origin+r['url'],source='Autos of Dallas',description='Dealer API listing. Title, history, HW4 and FSD not independently verified.',image=r.get('featuredphoto'),availability='Dealer active inventory',fees=fees))
        emit(f'Autos of Dallas: {len(rows)} records; '+('complete snapshot' if complete else 'partial snapshot'))
        return rows,complete
