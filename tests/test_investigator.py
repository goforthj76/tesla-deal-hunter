from app.investigator import investigate
P={'latitude':33.0146,'longitude':-97.0969,'radius':50}
def obs(vin,**kwargs):return {'source':'Dealer','url':'https://example.com/car','observed':'2026-10-22T01:00:00+00:00','data':{'vin':vin,**kwargs}}
def claim(field,value,kind='user_report'):return {'field':field,'value':value,'kind':kind,'source':'Regression case: user pasted report','observed':'2026-10-22T01:00:00+00:00'}
def test_reported_long_range_case():
 d=investigate([obs('5YJ3E1EA4RF862917',trim='Base')],[claim('trim','Long Range'),claim('hw4','FSD Computer 4')],P)
 assert any('MISIDENTIFIED' in x for x in d['opportunities'])
 assert d['hw4'].startswith('UNVERIFIED')
 assert d['fsd']=='PURCHASED FSD UNKNOWN'
def test_reported_orlando_case():
 d=investigate([obs('7SAYGDEE8PA163968',address='Dallas TX')],[claim('location','Orlando FL')],P)
 assert d['purchase_readiness']=='HOLD'
 assert any(c['field']=='location' for c in d['conflicts'])
def test_physical_hw_is_not_purchased_fsd():
 d=investigate([], [claim('hw4','Computer 4','physical_inspection')],P)
 assert 'PHYSICAL EVIDENCE' in d['hw4'] and d['fsd']=='PURCHASED FSD UNKNOWN'
def test_clean_title_and_loss_conflict():
 d=investigate([], [claim('history','clean title'),claim('history','total loss','history_report')],P)
 assert d['purchase_readiness']=='HOLD'
 assert any(c['field']=='history' for c in d['conflicts'])
def test_generic_trim_not_misidentification():
 d=investigate([obs('x',trim='Base')],[],P)
 assert not any('MISIDENTIFIED' in x for x in d['opportunities'])
def test_no_negative_results_not_clean():
 assert investigate([],[],P)['history'].startswith('UNKNOWN')
from test_core import database

def test_evidence_api_roundtrip(database,monkeypatch):
    from app import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main,'settings',lambda:main.Preferences().model_dump())
    monkeypatch.setenv('TDH_PASSWORD','fixture-password')
    c=TestClient(main.app,headers={'Authorization':'Basic '+__import__('base64').b64encode(b'hunter:fixture-password').decode()});vin='5YJ3E1EA4RF862917'
    r=c.post('/api/investigate/'+vin+'/evidence',json={'field':'trim','value':'Long Range','kind':'physical_inspection','source':'test inspection'})
    assert r.status_code==200
    identifier=r.json()['id']
    assert c.get('/api/investigate/'+vin).json()['claims'][0]['value']=='Long Range'
    assert c.post(f'/api/investigate/{vin}/evidence/{identifier}/retract').status_code==200
    assert c.get('/api/investigate/'+vin).json()['claims']==[]
    assert c.post('/api/investigate/INVALID/evidence',json={'field':'trim','value':'Base','kind':'seller','source':'test'}).status_code==422
from test_core import database, record

def test_populated_inventory_investigation(database,monkeypatch):
    from app import main
    monkeypatch.setattr(main,'settings',lambda:main.Preferences().model_dump())
    main.ingest('test',[record()],False)
    rows=main.vehicle_rows()
    assert len(rows)==1 and 'investigation' in rows[0]

def test_negated_and_generic_history_not_positive_proof():
 from app.investigator import adverse_claim
 assert not adverse_claim('No salvage or structural damage reported')
 assert not adverse_claim('Some vehicles may have a total loss')
 assert adverse_claim('Clean title. With a total loss on CARFAX')
 assert adverse_claim('No salvage title, but insurance total loss')

def test_false_clean_title_flag_is_review_not_verified_salvage():
 d=investigate([obs('x',carfax_clean_title=False)],[],P)
 assert d['purchase_readiness']=='HOLD'
 assert d['history'].startswith('UNKNOWN')
 assert any('flag is false' in w for w in d['warnings'])
 assert not any('verified adverse' in w.lower() for w in d['warnings'])

def test_invalid_feed_row_cannot_remove_previous_inventory(database):
 from app import main
 from app.database import Listing
 from sqlalchemy import select
 main.ingest('test',[record()],False)
 main.ingest('test',[{**record(),'vin':'bad'}],True)
 with database() as s:
  rows=list(s.scalars(select(Listing)))
  assert len(rows)==1 and rows[0].status=='ACTIVE'
