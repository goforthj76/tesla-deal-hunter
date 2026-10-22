import os
import threading
import webbrowser
from pathlib import Path
from dotenv import load_dotenv
import uvicorn
os.chdir(Path(__file__).resolve().parent)
load_dotenv('.env')
host=os.getenv('TDH_HOST','127.0.0.1')
if host not in ['127.0.0.1','localhost','::1'] and len(os.getenv('TDH_PASSWORD',''))<16:
    raise SystemExit('LAN mode requires TDH_PASSWORD with at least 16 characters in .env')
if __name__=='__main__':
    threading.Timer(2,lambda:webbrowser.open('http://127.0.0.1:'+os.getenv('TDH_PORT','8765'))).start()
    uvicorn.run('app.main:app',host=host,port=int(os.getenv('TDH_PORT','8765')),workers=1,access_log=False)
