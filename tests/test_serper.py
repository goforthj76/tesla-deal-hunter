from test_core import database,record,VIN
from app import main

def test_serper_automatic_evidence(database,monkeypatch):
 monkeypatch.setenv('SERPER_API_KEY','fixture')
 monkeypatch.setenv('SERPER_PREUSED_CALLS','0')
 main.ingest('test',[dict(record(),trim='RWD')],False)
 class R:
  status_code=200
  def json(self):return {'organic':[{'link':'https://example.com/car','title':VIN+' Long Range','snippet':VIN}]}
 monkeypatch.setattr(main.httpx,'post',lambda *a,**k:R())
 assert main.serper_research()['completed']==1
 assert main.discovery_state()['serper_reserved']==1
 assert any('MISIDENTIFIED' in x for x in main.investigate_vin(VIN)['opportunities'])
 assert main.serper_research()['status']=='COOLDOWN'

def test_serper_cap_and_error(database,monkeypatch):
 monkeypatch.setenv('SERPER_API_KEY','fixture')
 monkeypatch.setenv('SERPER_PREUSED_CALLS','2000')
 assert main.serper_research()['status']=='CAP REACHED'
 monkeypatch.setenv('SERPER_PREUSED_CALLS','0')
 main.ingest('test',[record()],False)
 class R:status_code=429
 monkeypatch.setattr(main.httpx,'post',lambda *a,**k:R())
 assert main.serper_research()['completed']==0
 d=main.discovery_state()
 assert d['serper_reserved']==1 and d['vin_jobs'][VIN]['status']=='ERROR'
 assert main.serper_research()['status']=='COOLDOWN'
