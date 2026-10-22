import math
import re
from typing import Any

def normalize_vin(value: Any) -> str:
    vin = re.sub(r'\s+', '', str(value)).upper()
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
        raise ValueError('Invalid 17-character VIN')
    values = {c: n for chars, n in [('AJ', 1), ('BKS', 2), ('CLT', 3), ('DMU', 4), ('ENV', 5), ('FW', 6), ('GPX', 7), ('HY', 8), ('RZ', 9)] for c in chars}
    total = sum((int(c) if c.isdigit() else values[c]) * w for c, w in zip(vin, [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]))
    if vin[8] != ('X' if total % 11 == 10 else str(total % 11)):
        raise ValueError('VIN check digit mismatch')
    return vin

def number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(',', '').replace('$', '')
    if not re.fullmatch(r'\d+(?:\.\d+)?(?:\s*(?:mi|miles))?', text, re.I):
        return None
    return float(re.match(r'\d+(?:\.\d+)?', text)[0])

def distance(lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None) -> float | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    try:
        a, b, c, d = map(math.radians, [float(lat1), float(lon1), float(lat2), float(lon2)])
        h = math.sin((c - a) / 2) ** 2 + math.cos(a) * math.cos(c) * math.sin((d - b) / 2) ** 2
        return round(3958.7613 * 2 * math.asin(min(1.0, math.sqrt(h))), 1)
    except Exception:
        return None

def compute_market_averages(listings: list[dict]) -> dict[tuple[str, int], dict]:
    """Group listings by (model, year) and calculate averages."""
    buckets: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for item in listings:
        m = item.get('model')
        y = item.get('year')
        p = item.get('price')
        mi = item.get('mileage')
        if m and y and p and p > 5000:
            key = (str(m).strip(), int(y))
            buckets.setdefault(key, []).append((float(p), float(mi) if mi is not None else 50000.0))

    results = {}
    for key, vals in buckets.items():
        avg_price = sum(v[0] for v in vals) / len(vals)
        avg_miles = sum(v[1] for v in vals) / len(vals)
        results[key] = {
            'avg_price': round(avg_price, 2),
            'avg_mileage': round(avg_miles, 1),
            'sample_count': len(vals)
        }
    return results

def check_hw4(listing: dict) -> tuple[bool, str]:
    year = listing.get('year') or 0
    model = str(listing.get('model') or '')
    desc = (str(listing.get('description') or '') + ' ' + str(listing.get('trim') or '')).lower()
    vin = str(listing.get('vin') or '').upper()

    # 1. Any 2024+ Tesla is 100% HW4
    if year >= 2024:
        return True, "HW4 CONFIRMED (2024+)"

    # 2. Explicit HW4 mentions in description, dealer notes, or options
    if 'hw4' in desc or 'hardware 4' in desc or 'ap04' in desc:
        return True, "HW4 CONFIRMED (Dealer/Spec)"

    # 3. 2023 Model S & Model X (switched to HW4 in Jan/Feb 2023)
    if year == 2023 and ('Model S' in model or 'Model X' in model):
        return True, "HW4 INFERRED (2023 S/X)"

    # 4. 2023 Model Y: Fremont cutover seq >= 780000, Austin cutover seq >= 120000
    if year == 2023 and 'Model Y' in model and len(vin) == 17:
        plant = vin[10]
        try:
            seq = int(vin[11:])
            if plant == 'A' and seq >= 120000:
                return True, "HW4 INFERRED (Late 2023 Austin)"
            if plant == 'F' and seq >= 780000:
                return True, "HW4 INFERRED (Late 2023 Fremont)"
        except ValueError:
            pass

    return False, "HW3 LIKELY"

def score_deal(listing: dict, market_averages: dict | None = None, settings: dict | None = None, price_history: list | None = None) -> dict:
    """
    Multi-factor 0-100 deal scoring engine.
    Factors:
    - Price vs Market (30 max)
    - Price Per Mile (20 max)
    - Mileage (15 max)
    - Distance (10 max)
    - Freshness (5 max)
    - Source Trust (5 max)
    - Budget Discount (15 max)
    Modifiers:
    - Recent price drop (+5)
    - HW4 generation (+5)
    - Salvage/Accident penalty (-15)
    - Missing photo (-5)
    """
    settings = settings or {}
    max_price = float(settings.get('max_price', 32000))
    ideal_max = float(settings.get('ideal_max', 28000))
    user_radius = float(settings.get('radius', 100))

    price = listing.get('price')
    mileage = listing.get('mileage')
    year = listing.get('year') or 0
    model = listing.get('model', 'Tesla')
    dealer = listing.get('dealer', '')
    source = listing.get('source', '')
    desc = (listing.get('description') or '').lower()
    dist = listing.get('distance')

    if dist is None and None not in (settings.get('latitude'), settings.get('longitude'), listing.get('latitude'), listing.get('longitude')):
        dist = distance(settings.get('latitude'), settings.get('longitude'), listing.get('latitude'), listing.get('longitude'))

    reasons: list[str] = []
    risks: list[str] = []
    breakdown: dict[str, float] = {}
    raw_score = 0.0

    # 1. Price vs Market (30 pts max)
    market_data = None
    if market_averages:
        market_data = market_averages.get((model, year))
    
    if market_data and price and price > 0:
        avg_p = market_data['avg_price']
        diff_pct = (avg_p - price) / avg_p
        diff_cash = avg_p - price
        if diff_pct >= 0.20:
            pts = 30.0
            reasons.append(f"${int(diff_cash):,} below market average ({int(diff_pct*100)}% off)")
        elif diff_pct >= 0.10:
            pts = 24.0
            reasons.append(f"${int(diff_cash):,} below market average")
        elif diff_pct >= 0.0:
            pts = 18.0
            reasons.append(f"Fair market price (${int(diff_cash):,} under avg)")
        elif diff_pct >= -0.10:
            pts = 8.0
        else:
            pts = 0.0
            risks.append("Priced significantly above market average")
    else:
        # Neutral if no market comps
        pts = 15.0
    breakdown['price_vs_market'] = pts
    raw_score += pts

    # 2. Price per Mile (20 pts max)
    if price and mileage and mileage > 1000:
        ppm = price / mileage
        if ppm < 0.75:
            pts = 20.0
            reasons.append(f"Low wear cost: ${ppm:.2f}/mile")
        elif ppm < 1.25:
            pts = 16.0
            reasons.append(f"Balanced price/mileage: ${ppm:.2f}/mi")
        elif ppm < 1.75:
            pts = 11.0
        elif ppm < 2.50:
            pts = 6.0
        else:
            pts = 2.0
    elif mileage and mileage <= 1000 and price and price <= max_price:
        pts = 18.0
        reasons.append("Ultra-low delivery mileage")
    else:
        pts = 8.0
    breakdown['price_per_mile'] = pts
    raw_score += pts

    # 3. Mileage (15 pts max)
    if mileage is not None:
        if mileage < 20000:
            pts = 15.0
            reasons.append(f"Only {int(mileage):,} miles")
        elif mileage < 40000:
            pts = 13.0
            reasons.append(f"{int(mileage):,} miles")
        elif mileage < 60000:
            pts = 10.0
        elif mileage < 80000:
            pts = 6.0
        elif mileage < 100000:
            pts = 3.0
        else:
            pts = 1.0
            risks.append(f"High mileage ({int(mileage):,} miles)")
    else:
        pts = 6.0
        risks.append("Mileage unlisted")
    breakdown['mileage'] = pts
    raw_score += pts

    # 4. Distance (10 pts max)
    if dist is not None:
        if dist <= 25:
            pts = 10.0
            reasons.append(f"Very close ({dist} mi away)")
        elif dist <= 50:
            pts = 8.0
            reasons.append(f"{dist} mi away")
        elif dist <= 75:
            pts = 6.0
        elif dist <= 100:
            pts = 4.0
        else:
            pts = 1.0
            risks.append(f"Farther out ({dist} mi)")
    else:
        pts = 4.0
    breakdown['distance'] = pts
    raw_score += pts

    # 5. Freshness (5 pts max)
    pts = 4.0
    breakdown['freshness'] = pts
    raw_score += pts

    # 6. Source Trust (5 pts max)
    if source == 'Tesla.com':
        pts = 5.0
        reasons.append("Official Tesla CPO warranty")
    elif source in ('Carvana', 'Autotrader', 'Cars.com', 'CarGurus'):
        pts = 3.5
    else:
        pts = 2.5
    breakdown['source_trust'] = pts
    raw_score += pts

    # 7. Discount from max budget (15 pts max)
    if price and price > 0:
        if price <= ideal_max:
            discount_pct = (max_price - price) / max_price
            pts = min(15.0, 10.0 + (discount_pct * 15.0))
            reasons.append(f"${int(price):,} fits well under target budget")
        elif price <= max_price:
            pts = 6.0
        else:
            pts = 0.0
            risks.append("Exceeds max budget")
    else:
        pts = 0.0
    breakdown['budget_fit'] = pts
    raw_score += pts

    # Modifiers
    modifier = 0.0
    if price_history and len(price_history) > 1:
        if price_history[-1] < price_history[0]:
            drop = price_history[0] - price_history[-1]
            modifier += 5.0
            reasons.append(f"Price reduced by ${int(drop):,}")

    is_hw4_car, hw4_tag = check_hw4(listing)
    if is_hw4_car:
        modifier += 15.0
        reasons.append(f"⚡ {hw4_tag}")
    elif settings.get('require_hw4'):
        modifier -= 30.0
        risks.append("Pre-2024 / HW3 model (HW4 required)")
    
    title_str = str(listing.get('title', '')).lower()
    dealer_str = str(listing.get('dealer', '')).lower()
    combined_text = f"{desc} {title_str} {str(listing.get('trim', '')).lower()}"
    adverse_kw = bool(re.search(r'\b(salvage|rebuilt|lemon|buyback|flood|total loss|frame damage|junk|parts only|structural|branded|trueframe)\b', combined_text))
    
    adverse = False
    if 'autosavvy' in dealer_str:
        adverse = True
        risks.append("TRASH VIN: AutoSavvy branded/rebuilt title inventory")
    elif listing.get('carfax_clean_title') is False:
        adverse = True
        risks.append("TRASH VIN: Carfax verified adverse/salvage title")
    elif adverse_kw:
        adverse = True
        risks.append("TRASH VIN: Title/damage keywords detected in listing")

    if adverse:
        final_score = 0
        cat = 'TRASH VIN'
    else:
        final_score = max(0, min(100, int(round(raw_score + modifier))))
        if final_score < 30:
            cat = 'REJECTED'
        elif final_score >= 82:
            cat = 'HOT DEAL'
        elif final_score >= 70:
            cat = 'GREAT DEAL'
        elif final_score >= 55:
            cat = 'GOOD DEAL'
        elif final_score >= 40:
            cat = 'FAIR'
        else:
            cat = 'OVERPRICED'

    return {
        'score': final_score,
        'category': cat,
        'breakdown': breakdown,
        'reasons': reasons,
        'risks': risks,
        'hw4': hw4_tag,
        'is_hw4': is_hw4_car,
        'distance': dist
    }
