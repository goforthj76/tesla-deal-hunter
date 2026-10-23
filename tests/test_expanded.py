import json
import asyncio
from app.expanded import plans,relevant,allowed,structured_rows,inspect_dealers
VIN='5YJ3E1EA7KF000001'

def test_plans_cover_markets_and_gemini():
    p=plans();assert len(p)>30
    assert any(x['domains']==['carmax.com'] for x in p)
    assert any(x['domains']==['huffinessubarucorinth.com'] for x in p)
    assert any('"Flower Mound" "Texas"' in x['query'] for x in p)

def test_relevance_and_fetch_allowlist():
    assert relevant({'title':'2024 Tesla Model Y Dallas Texas'})
    assert not relevant({'title':'Used cars Georgia'})
    assert not relevant({'title':'Tesla Model Y Atlanta Georgia'})
    assert allowed('https://www.huffinessubarucorinth.com/car')
    for u in ['http://www.huffinessubarucorinth.com/car','https://127.0.0.1/car','https://www.huffinessubarucorinth.com.evil.test/car','https://x:y@www.huffinessubarucorinth.com/car','https://www.huffinessubarucorinth.com:8080/car']:
        assert not allowed(u)

def test_structured_graph_mileage_and_invalid_vin():
    from test_core import VIN
    car={'@type':'Car','name':'2024 Tesla Model 3 Long Range','vehicleIdentificationNumber':VIN,'mileageFromOdometer':{'value':20000,'unitCode':'SMI'},'offers':{'price':30000,'priceCurrency':'USD','seller':{'name':'Dealer'}}}
    def parse(c):return structured_rows('<script type="application/ld+json">'+json.dumps({'@graph':[c]})+'</script>','https://www.huffinessubarucorinth.com/car')
    assert parse(car)[0]['mileage']==20000
    assert parse(car)[0]['latitude'] is None
    car['mileageFromOdometer']['unitCode']='KMT'
    assert parse(car)[0]['mileage'] is None
    car['vehicleIdentificationNumber']='invalid'
    assert parse(car)==[]

def test_partial_batch_failure_reserves_each_call(database,monkeypatch):
    from app import main
    monkeypatch.setenv('TAVILY_API_KEY','fixture')
    class R:
        status_code=429
    calls=[]
    monkeypatch.setattr(main.httpx,'post',lambda *a,**kw:calls.append(kw) or R())
    main.discover()
    assert len(calls)==1
    assert main.discovery_state()['reserved_calls']==1
    assert main.discovery_state()['status']=='UNAVAILABLE'

# Reuse database fixture from core tests.
from test_core import database
