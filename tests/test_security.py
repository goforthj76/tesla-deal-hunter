from fastapi.testclient import TestClient
from app import main

def test_missing_password_fails_closed(monkeypatch):
 monkeypatch.delenv('TDH_PASSWORD',raising=False)
 c=TestClient(main.app)
 assert c.get('/api/state').status_code==503
 assert c.post('/api/scan-all').status_code==503

def test_password_required_on_read_and_write(monkeypatch):
 monkeypatch.setenv('TDH_PASSWORD','fixture-password')
 c=TestClient(main.app)
 assert c.get('/').status_code==401
 assert c.post('/api/scan-all').status_code==401
 assert c.get('/',auth=('hunter','wrong')).status_code==401
 assert c.get('/',auth=('hunter','fixture-password')).status_code==200
 assert c.post('/api/scan-all',auth=('hunter','fixture-password'),headers={'Origin':'https://evil.example'}).status_code==403
