from app.vin_queue import sync_jobs,next_job,snippet_claims
from test_core import database,record,VIN
from app import main
from app.database import Vehicle

def test_all_vins_and_fairness():
 other='5YJ3E1EA4RF862917'
 jobs=sync_jobs({},[VIN,other,'bad'])
 assert len(jobs)==2
 first=next_job(jobs,1000000)
 jobs[first].update(last_success=999999,status='SEARCHED')
 assert next_job(jobs,1000000)!=first
 jobs[other if first==VIN else VIN].update(retry_after=1000010)
 assert next_job(jobs,1000000) is None

def test_snippets_are_not_physical_proof():
 r={'title':'Tesla Long Range '+VIN,'content':'','url':'https://example.com/car'}
 claims=snippet_claims(VIN,r,'now')
 assert claims[0]['kind']=='aggregator'
 assert claims[0]['value']=='Long Range'
 assert not snippet_claims(VIN,dict(r,content='5YJ3E1EA4RF862917'),'now')
 assert not snippet_claims(VIN,dict(r,title='Tesla Long Range'),'now')

def test_automatic_discovery_feeds_investigator(database,monkeypatch):
 monkeypatch.setenv('TAVILY_API_KEY','fixture')
 main.ingest('test',[dict(record(),trim='RWD',year=2020,price=40000)],False)
 class Response:
  status_code=200
  def json(self):return {'results':[{'url':'https://example.com/other','title':VIN+' Long Range Tesla','content':'Seller listing '+VIN}]}
 monkeypatch.setattr(main.httpx,'post',lambda *a,**k:Response())
 main.discover()
 q=main.vin_queue_status()
 assert q['total']==1 and q['ever_searched']==1
 assert q['jobs'][VIN]['status']=='SEARCHED'
 d=main.investigate_vin(VIN)
 assert any('MISIDENTIFIED' in x for x in d['opportunities'])
 assert d['hw4'].startswith('UNVERIFIED')
 assert any('MISIDENTIFIED' in x for x in main.vehicle_rows()[0]['investigation']['opportunities'])

def test_failed_search_not_completed(database,monkeypatch):
 monkeypatch.setenv('TAVILY_API_KEY','fixture')
 main.ingest('test',[record()],False)
 main.save_discovery({'month':main.now()[:7],'cursor':1})
 class Response:status_code=429
 monkeypatch.setattr(main.httpx,'post',lambda *a,**k:Response())
 main.discover()
 q=main.vin_queue_status()
 assert q['ever_searched']==0 and q['counts']['ERROR']==1
 assert main.discovery_state()['reserved_calls']==1


def test_no_matches_not_clearance_and_no_repeat(database,monkeypatch):
 monkeypatch.setenv('TAVILY_API_KEY','fixture')
 main.ingest('test',[record()],False)
 class Response:
  status_code=200
  def json(self):return {'results':[{'url':'https://example.com/general','title':'Long Range Tesla','content':'No specific VIN'}]}
 monkeypatch.setattr(main.httpx,'post',lambda *a,**k:Response())
 main.discover()
 job=main.vin_queue_status()['jobs'][VIN]
 assert job['status']=='NO_EXACT_RESULTS' and job['attempts']==1
 assert not job['claims']
 assert main.investigate_vin(VIN)['history'].startswith('UNKNOWN')
 assert next_job({VIN:job},main.time.time()) is None
