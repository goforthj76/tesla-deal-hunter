import json
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app import main
from app.database import Base, Vehicle, Listing, Price, Source
from app.analysis import normalize_vin, number, distance, assess
from app.collectors import parse_product

VIN='5YJ3E1EA9SF900678'
def record(price=30000,url='https://example.com/car'):
    return dict(vin=VIN,price=price,url=url,year=2025,model='Model 3',trim='Long Range',drivetrain='RWD',mileage=20000,description='',latitude=33.0146,longitude=-97.0969)

@pytest.fixture
def database(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'test.db'));Base.metadata.create_all(engine)
    sessions=sessionmaker(engine,expire_on_commit=False);monkeypatch.setattr(main,'Session',sessions)
    with sessions.begin() as s:s.add(Source(name='test'))
    return sessions

def test_vin():
    assert normalize_vin(' '+VIN.lower())==VIN
    with pytest.raises(ValueError):normalize_vin(VIN[:-1]+'9')
    with pytest.raises(ValueError):normalize_vin('bad')

@pytest.mark.parametrize('value,expected',[('$29,995',29995),('24,819 miles',24819),('call',None),('300/month',None),(None,None)])
def test_numbers(value,expected):assert number(value)==expected

def test_distance():
    assert distance(33,-97,33,-97)==0
    assert distance(33,-97,None,None) is None
    assert 68<distance(33,-97,34,-97)<70

def test_gates_and_uncertainty():
    p=main.Preferences().model_dump()
    assert assess(record(),p)['category']=='WATCH'
    assert assess(record(36001),p)['category']=='REJECTED'
    d=record();d['year']=2022
    assert assess(d,p)['category']=='WATCH'
    d=record();d['latitude']=35
    assert assess(d,p)['category']=='REJECTED'
    d=record();d['description']='rebuilt title'
    assert assess(d,p)['category']=='MANUAL REVIEW'
    assert assess(record(),p)['fsd_credit']==0

def test_wix_concatenation_and_fsd():
    d={'@type':'Product','name':'2025 Tesla Model 3 Long Range RWD','description':'Vin: '+VIN+'20,000 milesFSD paid','offers':{'price':'30000'}}
    r=parse_product('<script type="application/ld+json">'+json.dumps(d)+'</script>','https://example.com')
    assert r['mileage']==20000
    assert assess(r,main.Preferences().model_dump())['fsd']=='SELLER CLAIM: PAID FSD'

def test_dedupe_prices_removal(database):
    main.ingest('test',[record(),record(url='https://example.com/second')],True)
    main.ingest('test',[record(),record(url='https://example.com/second')],True)
    with database() as s:
        assert len(list(s.scalars(select(Vehicle))))==1
        assert len(list(s.scalars(select(Listing))))==2
        assert len(list(s.scalars(select(Price))))==2
    main.ingest('test',[record(29000)],False)
    with database() as s:
        assert len(list(s.scalars(select(Price))))==3
        assert all(l.status=='ACTIVE' for l in s.scalars(select(Listing)))
    main.ingest('test',[],True);main.ingest('test',[],True)
    with database() as s:assert all(l.status=='REMOVED / AVAILABILITY UNKNOWN' for l in s.scalars(select(Listing)))

def test_source_failure_isolated(database,monkeypatch):
    import asyncio
    with database.begin() as s:s.add(Source(name='Autohub Texas'))
    monkeypatch.setattr(main,'settings',lambda:main.Preferences().model_dump())
    async def fail(emit):raise ValueError('Fixture source offline')
    monkeypatch.setattr(main,'autohub',fail)
    asyncio.run(main.scan_source('Autohub Texas'))
    with database() as s:
        assert s.get(Source,'Autohub Texas').status=='UNAVAILABLE'
        assert s.get(Source,'Autohub Texas').next_scan>0

def test_settings_validation():
    with pytest.raises(ValueError):main.Preferences(dealer_interval_minutes=1)


def test_unknown_distance_needs_review():
    d=record();d['latitude']=None
    assert assess(d,main.Preferences().model_dump())['category']=='MANUAL REVIEW'

def test_total_loss_needs_review():
    d=record();d['description']='Clean title but insurance total loss disclosed'
    assert assess(d,main.Preferences().model_dump())['category']=='MANUAL REVIEW'

def test_pagination_beyond_250(monkeypatch):
    import asyncio
    from app import collectors
    class Response:
        status_code=200
        def __init__(self,start): self.start=start
        def json(self):
            return {'num_found':301,'listings':[{'vin':VIN,'vdp_url':'https://example.com/'+str(i),'build':{'year':2025,'model':'Model 3'},'price':30000,'miles':20000,'dealer':{'latitude':33,'longitude':-97}} for i in range(self.start,min(self.start+50,301))]}
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def get(self,url,params): return Response(params['start'])
    async def sleep(_): pass
    monkeypatch.setattr(collectors.httpx,'AsyncClient',Client)
    monkeypatch.setattr(collectors.asyncio,'sleep',sleep)
    calls=[]
    rows,complete=asyncio.run(collectors.marketcheck('test',main.Preferences().model_dump(),lambda _:None,lambda:calls.append(1)))
    assert complete and len(rows)==301 and len(calls)==7


def test_marketcheck_disabled_without_permission(monkeypatch):
    import asyncio
    monkeypatch.setattr(main,'MARKETCHECK_ARCHIVE_ENABLED',False)
    events=[];monkeypatch.setattr(main,'activity',events.append)
    asyncio.run(main.scan_source('MarketCheck'))
    assert 'disabled' in events[0]

def test_hunt_counts_persist(database):
    main.start_hunt()
    assert main.start_hunt()['status']=='already running'
    main.ingest('test',[record()],True)
    summary=main.finish_hunt()
    assert summary=={'observations':1,'unique_vins':1,'scans':1}
