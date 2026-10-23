"""Evidence assessment; no VIN decoder is treated as physical proof."""
from datetime import datetime, timezone
import re
from .analysis import distance

FIELDS={'trim','drivetrain','year','mileage','price','location','hw4','fsd','history','availability'}
KINDS={'seller','aggregator','user_report','physical_inspection','history_report'}

def adverse_claim(value):
    val_str = str(value).lower()
    auction_domains = ['vehileaks', 'cleanautohistory', 'bid.cars', 'copart', 'iaai', 'salvagedb', 'stat.vin', 'poctra', 'badvin', 'carcheck.vin', 'autoastat']
    if any(dom in val_str for dom in auction_domains):
        return True
    # Negation applies to its own clause, not every disclosure in the sentence.
    clauses = re.split(r'[.;,\n]|\bbut\b|\band\b', val_str)
    for clause in clauses:
        for match in re.finditer(r'\b(total loss|salvage|rebuilt|structural damage|frame damage|flood|water damage|lemon|hail damage|certificate of destruction|junk title|collision loss|insurance loss|auction record|estimated repair cost)\b', clause):
            prefix = clause[:match.start()]
            if re.search(r'\b(some vehicles|may have|might have|could have)\b', clause): continue
            if re.search(r'\b(no|not|never|without)\b[^.!?]{0,55}$', prefix): continue
            return True
    return False

def decode_tesla_motor(vin):
    if not isinstance(vin, str) or len(vin) != 17:
        return {}
    model_char = vin[3].upper() # 4th char: '3' or 'Y'
    motor_char = vin[7].upper() # 8th char
    
    if model_char == '3':
        if motor_char in ('A', 'J', 'R', 'S'):
            return {'model': '3', 'motor_code': motor_char, 'drivetrain': 'RWD', 'motor_type': 'Single Motor Standard', 'is_single': True, 'is_dual': False, 'is_perf': False}
        elif motor_char == 'B':
            return {'model': '3', 'motor_code': motor_char, 'drivetrain': 'AWD', 'motor_type': 'Dual Motor (Long Range)', 'is_single': False, 'is_dual': True, 'is_perf': False}
        elif motor_char in ('C', 'F', '4', 'L'):
            return {'model': '3', 'motor_code': motor_char, 'drivetrain': 'AWD', 'motor_type': 'Dual Motor (Performance)', 'is_single': False, 'is_dual': True, 'is_perf': True}
        elif motor_char in ('D', 'E'):
            return {'model': '3', 'motor_code': motor_char, 'drivetrain': 'AWD', 'motor_type': 'Dual Motor', 'is_single': False, 'is_dual': True, 'is_perf': False}
    elif model_char == 'Y':
        if motor_char in ('A', 'J'):
            return {'model': 'Y', 'motor_code': motor_char, 'drivetrain': 'RWD', 'motor_type': 'Single Motor Standard', 'is_single': True, 'is_dual': False, 'is_perf': False}
        elif motor_char == 'D':
            # 2024 Model Y: 8th digit D is Single Motor Long Range RWD
            return {'model': 'Y', 'motor_code': motor_char, 'drivetrain': 'RWD', 'motor_type': 'Single Motor Long Range', 'is_single': True, 'is_dual': False, 'is_perf': False}
        elif motor_char == 'E':
            return {'model': 'Y', 'motor_code': motor_char, 'drivetrain': 'AWD', 'motor_type': 'Dual Motor (Long Range)', 'is_single': False, 'is_dual': True, 'is_perf': False}
        elif motor_char in ('F', 'C'):
            return {'model': 'Y', 'motor_code': motor_char, 'drivetrain': 'AWD', 'motor_type': 'Dual Motor (Performance)', 'is_single': False, 'is_dual': True, 'is_perf': True}
            
    return {'model': model_char, 'motor_code': motor_char, 'drivetrain': 'UNKNOWN', 'motor_type': 'Unknown', 'is_single': False, 'is_dual': False, 'is_perf': False}

AGGREGATORS={'MarketCheck','TrueCar','Cars.com','CarGurus','Autotrader'}

def location_key(value):
    text=re.sub(r'[^a-z0-9 ]',' ',str(value).lower())
    return ' '.join(text.split())

def precise_location(value):
    return location_key(value) not in {'dfw','dfw area','dallas fort worth','texas','tx','unknown','dallas area'}

def generic_trim(value):
    text=str(value).strip().lower()
    return text in {'base','standard','standard range','rwd','rear wheel drive','model 3','unknown'}

def investigate(observations, claims, preferences):
    facts=[]
    for o in observations:
        d=o['data'];base={'source':o['source'],'url':o.get('url',''),'observed':o['observed'],'kind':'aggregator' if o['source'] in AGGREGATORS else 'seller'}
        for field in ['trim','drivetrain','year','mileage','price']:
            if d.get(field) not in (None,''):facts.append(dict(base,field=field,value=str(d[field])))
        if d.get('address'):facts.append(dict(base,field='location',value=d['address']))
    facts.extend(claims)
    conflicts=[];opportunities=[];warnings=[]
    by={f:[c for c in facts if c['field']==f] for f in FIELDS}
    for field in ['trim','drivetrain','year','location','hw4','fsd','history','availability']:
        vals={str(c['value']).strip().lower() for c in by[field]}
        if field=='location':vals={location_key(c['value']) for c in by[field] if precise_location(c['value'])}
        if len(vals)>1:conflicts.append({'field':field,'values':sorted(vals),'message':field.upper()+' DISAGREEMENT — compare dates and evidence'})
    for field,tolerance in [('mileage',1000),('price',500)]:
        values=[]
        for c in by[field]:
            try:values.append(float(str(c['value']).replace(',','').replace('$','')))
            except ValueError:pass
        if len(values)>1 and max(values)-min(values)>tolerance:conflicts.append({'field':field,'values':values,'message':field.upper()+' VARIATION — may reflect elapsed time or pricing terms'})
    trims=by['trim']
    drivetrains=by['drivetrain']
    obs_sorted = sorted(observations, key=lambda o: str(o.get('observed', '')), reverse=True)
    current_obs = obs_sorted[0].get('data', {}) if obs_sorted else {}
    curr_trim = str(current_obs.get('trim', '')).strip().lower()
    curr_dt = str(current_obs.get('drivetrain', '')).strip().lower()
    curr_desc = str(current_obs.get('description', ''))

    curr_is_generic = generic_trim(curr_trim)
    curr_is_rwd = curr_dt in ('rwd', 'rear wheel drive', '') or curr_is_generic
    curr_is_perf = 'performance' in curr_trim or 'plaid' in curr_trim
    curr_is_lr = 'long range' in curr_trim
    curr_is_awd = curr_dt in ('awd', 'all-wheel drive', '4wd', 'dual motor') or 'dual motor' in curr_trim or 'awd' in curr_trim
    curr_has_fsd = bool(re.search(r'(full self driving|fsd)\s+(paid|included|equipped|purchased)', curr_desc, re.I))

    all_vins = {o.get('data', {}).get('vin') for o in observations if o.get('data', {}).get('vin')}
    all_vins.update(c.get('vin') for c in claims if c.get('vin'))
    primary_vin = next((v for v in all_vins if isinstance(v, str) and len(v) == 17), None)
    vin_motor = decode_tesla_motor(primary_vin) if primary_vin else {}

    all_trims = [str(c['value']).lower() for c in by['trim']]
    all_dts = [str(c['value']).lower() for c in by['drivetrain']]
    all_fsd = [str(c['value']).lower() for c in by['fsd']]
    all_descs = [str(o.get('data', {}).get('description', '')) for o in observations] + [str(c.get('excerpt', '')) for c in claims]

    prior_has_perf = any('performance' in t or 'plaid' in t for t in all_trims)
    prior_has_lr = any('long range' in t for t in all_trims)
    prior_has_awd = any('dual motor' in t or 'awd' in t for t in all_trims + all_dts) or any('dual motor' in d.lower() or 'all-wheel drive' in d.lower() for d in all_descs)
    prior_has_fsd = any('paid' in f or 'included' in f or 'purchased' in f for f in all_fsd) or any(bool(re.search(r'(full self driving|fsd)\s+(paid|included|equipped|purchased)', d, re.I)) and not re.search(r'(subscription|monthly|trial)', d, re.I) for d in all_descs)

    # Adverse history & AutoSavvy checks
    is_autosavvy = any('autosavvy' in str(o.get('data', {}).get('dealer', '')).lower() or 'autosavvy' in str(o.get('url', '')).lower() for o in observations)
    history = [c for c in by['history'] if adverse_claim(c['value'])]
    has_salvage = bool(history) or is_autosavvy

    if is_autosavvy:
        warnings.append('🚨 AutoSavvy branded/rebuilt title dealer — vehicle excluded pending clean title certification')
    if history:
        warnings.append('🚨 Adverse history detected (auction damage / salvage / total loss); excluded pending resolution')
    for o in observations:
        if o['data'].get('carfax_clean_title') is False:
            warnings.append('Source clean-title flag is false; meaning and VIN-specific history must be verified')
        text = o['data'].get('description', '')
        if adverse_claim(text):
            warnings.append('Seller text contains adverse-history wording; review whether VIN-specific')

    # Opportunities only granted to non-salvage vehicles!
    if not has_salvage:
        # 1. Hidden Performance
        if vin_motor.get('is_perf') and not curr_is_perf:
            opportunities.append('🚀 HIDDEN PERFORMANCE: Factory VIN motor code (' + vin_motor.get('motor_code', '') + ') indicates Performance Dual Motor, but listing advertises as standard trim')
        elif prior_has_perf and vin_motor.get('is_single'):
            warnings.append('⚠️ CONFIGURATION CONFLICT: Web record mentions Performance, but VIN 8th digit \'' + vin_motor.get('motor_code', '') + '\' specifies Single Motor (RWD). Requires in-person screen verification.')
        elif prior_has_perf and not curr_is_perf:
            opportunities.append('🚀 HIDDEN PERFORMANCE: Prior records mention Performance, but current dealer advertises as standard trim — requires screen verification')

        # 2. Hidden Long Range (e.g. 5YJ3E1EB4RF833863 with motor code B)
        if vin_motor.get('is_dual') and (curr_is_generic or curr_is_rwd) and not curr_is_lr and not curr_is_perf:
            opportunities.append('💎 POSSIBLE MISIDENTIFIED LONG RANGE: Factory VIN motor code (' + vin_motor.get('motor_code', '') + ') indicates Dual Motor Long Range hardware, but current dealer advertises as Base/RWD!')
        elif (prior_has_lr or (vin_motor.get('is_dual') and curr_is_generic)) and not curr_is_lr and not curr_is_perf:
            if vin_motor.get('is_single'):
                opportunities.append('💎 POSSIBLE MISIDENTIFIED LONG RANGE LEAD: Listing advertises as Base/RWD, but search snippet mentions Long Range — check software screen in person (VIN indicates Single Motor; unverified lead)')
            else:
                opportunities.append('💎 POSSIBLE MISIDENTIFIED LONG RANGE: Prior records indicate Long Range, but current dealer advertises as generic/Base model')

        # 3. Hidden AWD / Dual Motor (covers old listing says AWD/Dual Motor, current says RWD)
        if vin_motor.get('is_dual') and curr_is_rwd and not curr_is_awd and not curr_is_lr and not curr_is_perf:
            opportunities.append('⚡ HIDDEN AWD / DUAL MOTOR: Factory VIN motor code indicates Dual Motor AWD, but current listing lists as RWD/Base')
        elif prior_has_awd and not curr_is_awd and not curr_is_lr and not curr_is_perf and not vin_motor.get('is_single'):
            opportunities.append('⚡ HIDDEN AWD / DUAL MOTOR: Prior records mention factory Dual Motor AWD, but current listing lists as RWD/Base')

        # 4. Hidden Permanent FSD (covers old listing says FSD, current listing dropped it)
        if prior_has_fsd and not curr_has_fsd:
            opportunities.append('🧠 PERMANENT PURCHASED FSD: Prior listing or web records indicate Paid FSD included, but current dealer omitted it ($8,000-$15,000 added value)')
        elif prior_has_fsd:
            opportunities.append('🧠 PERMANENT PURCHASED FSD DETECTED: Full Self-Driving included ($8,000-$15,000 added value)')

    if not opportunities and any(generic_trim(c['value']) for c in trims):
        opportunities.append('Trim verification task: generic trim alone is not evidence of Long Range')

    geographic_conflict=any(c['field']=='location' for c in conflicts)
    if geographic_conflict:warnings.append('Conflicting location claims; do not plan a visit until physical store is verified')
    out_of_area=False
    for o in observations:
        d=o['data']
        try:dist=distance(preferences['latitude'],preferences['longitude'],d.get('latitude'),d.get('longitude'))
        except (ValueError,TypeError):dist=None
        if dist is not None and dist>preferences['radius']:out_of_area=True
    if out_of_area:warnings.append('At least one observed location is outside the configured radius')

    physical=[c for c in claims if c.get('kind')=='physical_inspection']
    hw=[c for c in physical if c['field']=='hw4'];fsd=[c for c in physical if c['field']=='fsd']
    hw_label=('USER-RECORDED PHYSICAL EVIDENCE: '+hw[-1]['value']) if hw else 'UNVERIFIED — model year and seller wording are not confirmation'
    fsd_label=('USER-RECORDED SOFTWARE EVIDENCE: '+fsd[-1]['value']) if fsd else 'PURCHASED FSD UNKNOWN'
    recent=False
    for o in observations:
        try:recent |= o['source'] not in AGGREGATORS and 0<=(datetime.now(timezone.utc)-datetime.fromisoformat(o['observed'])).total_seconds()<86400
        except (ValueError,TypeError):pass
    return {'claims':facts,'conflicts':conflicts,'opportunities':opportunities,'warnings':list(dict.fromkeys(warnings)),
      'investigation_priority':'HIGH' if warnings or conflicts or any('MISIDENTIFIED' in x for x in opportunities) else 'NORMAL',
      'purchase_readiness':'HOLD' if warnings else 'NEEDS VERIFICATION','hw4':hw_label,'fsd':fsd_label,
      'history':'ADVERSE EVIDENCE / REVIEW' if history else 'UNKNOWN — no authoritative clearance established',
      'freshness':'RECENT SOURCE OBSERVATION; NOT AVAILABILITY CONFIRMATION' if recent else 'STALE OR UNVERIFIED',
      'checklist':['Confirm exact VIN on car and documents','Software screen: trim and odometer','Additional Vehicle Information: AI computer','Verify purchased software separately from trial/subscription','Obtain full history report and independent inspection','Confirm physical store and availability','Get itemized buyer’s order including fees']}
