import os
import sys
import threading
import webbrowser
import ctypes
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

if sys.platform == 'win32':
    try:
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    except Exception:
        pass

os.chdir(Path(__file__).resolve().parent)
load_dotenv('.env')
host = os.getenv('TDH_HOST', '0.0.0.0')

if __name__ == '__main__':
    port = int(os.getenv('TDH_PORT', '8765'))
    token = os.getenv('NGROK_AUTHTOKEN')
    if token:
        try:
            from pyngrok import ngrok
            try:
                ngrok.set_auth_token(token)
            except Exception:
                pass
            tunnel = ngrok.connect(port)
            print("\n=======================================================", flush=True)
            print(f"  PERMANENT PUBLIC URL: {tunnel.public_url}", flush=True)
            print(f"  CHATGPT EVAL URL:    {tunnel.public_url}/eval", flush=True)
            print("=======================================================\n", flush=True)
        except Exception as e:
            print(f"Note: ngrok tunnel could not be started: {e}", flush=True)

    threading.Timer(2, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    try:
        uvicorn.run('app.main:app', host=host, port=port, workers=1, access_log=False)
    finally:
        if token:
            try:
                from pyngrok import ngrok
                ngrok.kill()
            except Exception:
                pass
