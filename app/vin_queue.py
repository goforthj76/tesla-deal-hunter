"""Fair, persistent exact-VIN research; search snippets are leads, not proof."""
import re
from .analysis import normalize_vin
REFRESH_SECONDS=30*86400
RETRY_SECONDS=4*3600

def sync_jobs(jobs, vins):
    result=dict(jobs)
    for raw in vins:
        try:vin=normalize_vin(raw)
        except ValueError:continue
        result.setdefault(vin, {'status':'PENDING','attempts':0,'last_success':0,'retry_after':0,'claims':[],'leads':[],'priority':1})
    return result

def next_job(jobs, clock):
    eligible=[(bool(j.get('last_success')), j.get('priority', 1), j.get('attempts',0) if not j.get('last_success') else j['last_success'],vin) for vin,j in jobs.items() if j.get('retry_after',0)<=clock and (not j.get('last_success') or clock-j['last_success']>=REFRESH_SECONDS)]
    return min(eligible)[3] if eligible else None

def snippet_claims(vin, result, observed):
    title=str(result.get('title','')); content=str(result.get('content',''))
    text=title+' '+content
    tokens=set(re.findall(r'(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])',text.upper()))
    # Multi-car pages cannot safely associate a trim with one VIN.
    if tokens != {vin}:return []
    trim=None
    if re.search(r'\blong[ -]range\b|\bdual[ -]motor\b|\ball[ -]wheel drive\b|\bawd\b',text,re.I):
        trim='Performance' if re.search(r'\bperformance\b',text,re.I) else 'Long Range'
    elif re.search(r'\bperformance\b',text,re.I):
        trim='Performance'
    elif re.search(r'\bRWD\b|rear[ -]wheel drive|\bstandard[ -]range\b|\bbase\b',text,re.I):
        trim='RWD'
    if not trim:return []
    return [dict(field='trim',value=trim,kind='aggregator',source='Unverified web search snippet',url=result['url'],observed=observed,excerpt=text[:1600])]

def summary(jobs, clock):
    counts={s:sum(j['status']==s for j in jobs.values()) for s in ['PENDING','SEARCHING','SEARCHED','NO_EXACT_RESULTS','ERROR']}
    return dict(total=len(jobs),counts=counts,ever_searched=sum(bool(j.get('last_success')) for j in jobs.values()),due=sum(not j.get('last_success') or clock-j['last_success']>=REFRESH_SECONDS for j in jobs.values()),refresh_days=30)
