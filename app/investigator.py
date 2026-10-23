"""Evidence assessment; no VIN decoder is treated as physical proof."""
from datetime import datetime, timezone
import re
from .analysis import distance

FIELDS={'trim','drivetrain','year','mileage','price','location','hw4','fsd','history','availability'}
KINDS={'seller','aggregator','user_report','physical_inspection','history_report'}

def adverse_claim(value):
    # Negation applies to its own clause, not every disclosure in the sentence.
    clauses=re.split(r'[.;,\n]|\bbut\b|\band\b',str(value).lower())
    for clause in clauses:
        for match in re.finditer(r'\b(total loss|salvage|rebuilt|structural damage|flood)\b',clause):
            prefix=clause[:match.start()]
            if re.search(r'\b(some vehicles|may have|might have|could have)\b',clause):continue
            if re.search(r'\b(no|not|never|without)\b[^.!?]{0,55}$',prefix):continue
            return True
    return False

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
    if any(generic_trim(c['value']) for c in trims) and any('long range' in c['value'].lower() for c in trims):
        opportunities.append('POSSIBLE MISIDENTIFIED LONG RANGE: conflicting trim evidence; verify Software screen and drivetrain')
    elif any(generic_trim(c['value']) for c in trims):
        opportunities.append('Trim verification task: generic trim alone is not evidence of Long Range')
    history=[c for c in by['history'] if adverse_claim(c['value'])]
    if history:warnings.append('Adverse history evidence recorded; excluded pending resolution')
    for o in observations:
        if o['data'].get('carfax_clean_title') is False:
            warnings.append('Source clean-title flag is false; meaning and VIN-specific history must be verified')
        text=o['data'].get('description','')
        # Positive disclosure only; generic keyword hits remain review tasks, not facts.
        if adverse_claim(text):
            warnings.append('Seller text contains adverse-history wording; review whether VIN-specific')
    geographic_conflict=any(c['field']=='location' for c in conflicts)
    if geographic_conflict:warnings.append('Conflicting location claims; do not plan a visit until physical store is verified')
    out_of_area=False
    for o in observations:
        d=o['data']
        try:dist=distance(preferences['latitude'],preferences['longitude'],d.get('latitude'),d.get('longitude'))
        except (ValueError,TypeError):dist=None
        if dist is not None and dist>preferences['radius']:out_of_area=True
    if out_of_area:warnings.append('At least one observed location is outside the configured radius')
    if any('autosavvy' in str(o['data'].get('dealer','')).lower() for o in observations):
        warnings.append('Dealer specializes in branded-title inventory; VIN-specific history required, not proof of this car’s title')
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
