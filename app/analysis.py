import math
import re

def normalize_vin(value):
    vin = re.sub(r'\s+', '', str(value)).upper()
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
        raise ValueError('Invalid 17-character VIN')
    values = {c: n for chars, n in [('AJ',1),('BKS',2),('CLT',3),('DMU',4),('ENV',5),('FW',6),('GPX',7),('HY',8),('RZ',9)] for c in chars}
    total = sum((int(c) if c.isdigit() else values[c])*w for c,w in zip(vin,[8,7,6,5,4,3,2,10,0,9,8,7,6,5,4,3,2]))
    if vin[8] != ('X' if total % 11 == 10 else str(total % 11)):
        raise ValueError('VIN check digit mismatch')
    return vin

def number(value):
    if value is None: return None
    text = str(value).strip().replace(',', '').replace('$', '')
    if not re.fullmatch(r'\d+(?:\.\d+)?(?:\s*(?:mi|miles))?', text, re.I): return None
    return float(re.match(r'\d+(?:\.\d+)?', text)[0])

def distance(lat1, lon1, lat2, lon2):
    if lat2 is None or lon2 is None: return None
    a,b,c,d = map(math.radians, [lat1,lon1,float(lat2),float(lon2)])
    h = math.sin((c-a)/2)**2 + math.cos(a)*math.cos(c)*math.sin((d-b)/2)**2
    return 3958.7613 * 2 * math.asin(min(1, math.sqrt(h)))

def assess(d, settings):
    reasons, risks = [], []
    price, miles, year = d.get('price'), d.get('mileage'), d.get('year') or 0
    dist = distance(settings['latitude'], settings['longitude'], d.get('latitude'),d.get('longitude'))
    hw = 'UNKNOWN'
    # Conservative screening only. No undocumented VIN thresholds or invented probabilities.
    if d.get('model') == 'Model Y' and year <= 2022 and year > 0: hw = 'HW3 LIKELY'
    if d.get('model') == 'Model 3' and year <= 2023 and year > 0: hw = 'HW3 LIKELY'
    if d.get('model') in ['Model 3','Model Y'] and year >= 2024: hw = 'HW4 GENERATION INFERRED'
    if price is not None and price > settings['max_price']: reasons.append('Advertised price exceeds maximum')
    if dist is not None and dist > settings['radius']: reasons.append('Outside radius')
    if d.get('model') not in settings['models']: reasons.append('Model not selected')
    if settings['require_hw4'] and hw == 'HW3 LIKELY': risks.append('Model-year screening indicates HW3; direct confirmation required before rejection')
    if d.get('adverse_confirmed'): reasons.append('Confirmed disqualifying history')
    text = d.get('description','').lower()
    concern = bool(re.search(r'\b(salvage|rebuilt|buyback|lemon|flood|total loss|structural damage|odometer discrepancy)\b',text))
    risks += ['Title and accident history not independently verified', 'Battery health UNKNOWN', 'Dealer fees unknown', 'HW computer needs direct vehicle evidence']
    if concern: risks.append('Potential adverse wording in listing; manual interpretation needed')
    if dist is None: risks.append('Distance unverified')
    if price is None: risks.append('Price missing')
    if miles is None: risks.append('Mileage missing')
    if miles is not None and miles > settings['preferred_mileage']: risks.append('Mileage above preference')
    # A transparent preference score, explicitly NOT a comparable-market deal score.
    score = (30 if price is not None and price <= settings['ideal_max'] else 15 if price is not None and price <= settings['max_price'] else 0)
    score += 20 if miles is not None and miles < 40000 else 10 if miles is not None and miles < 50000 else 0
    score += 15 if d.get('model') == 'Model Y' else 5
    score += 15 if 'long range' in d.get('trim','').lower() else 0
    score += 10 if d.get('drivetrain') == 'AWD' else 0
    score += 10 if year >= 2024 else 0
    fsd = 'SELLER CLAIM: PAID FSD' if re.search(r'(full self driving|fsd)\s+(paid|included)',text) else 'SUBSCRIPTION MENTIONED' if re.search(r'fsd.{0,35}(monthly|month|subscription)',text) else 'UNKNOWN'
    return {'category':'REJECTED' if reasons else 'MANUAL REVIEW' if concern or dist is None else 'WATCH','preference_score':score,'hw4':hw,'fsd':fsd,'distance':round(dist,1) if dist is not None else None,'reasons':reasons,'risks':risks,'market_value':None,'battery_health':None,'history':'UNKNOWN','fsd_credit':0,'fsd_subscription_comparison':settings['fsd_monthly']*settings['ownership_months']}
