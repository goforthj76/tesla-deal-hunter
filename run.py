import os
import sys
import threading
import webbrowser
import ctypes
import subprocess
import re
import time
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

def start_cloudflare_tunnel(port: int):
    cf_path = Path(__file__).resolve().parent / ('cloudflared.exe' if sys.platform == 'win32' else 'cloudflared')
    if not cf_path.exists():
        return None, None
    try:
        proc = subprocess.Popen(
            [str(cf_path), 'tunnel', '--url', f'http://127.0.0.1:{port}'],
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            bufsize=1
        )
        url = None
        start = time.time()
        while time.time() - start < 15:
            line = proc.stderr.readline()
            if not line:
                break
            if 'trycloudflare.com' in line:
                m = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if m:
                    url = m.group(0)
                    break
        return proc, url
    except Exception as e:
        print(f"Note: Cloudflare tunnel could not be started: {e}", flush=True)
        return None, None

if __name__ == '__main__':
    port = int(os.getenv('TDH_PORT', '8765'))
    token = os.getenv('NGROK_AUTHTOKEN')
    public_url = None
    cf_proc, cf_url = start_cloudflare_tunnel(port)
    if cf_url:
        public_url = cf_url
    elif token:
        try:
            from pyngrok import ngrok
            try:
                ngrok.set_auth_token(token)
            except Exception:
                pass
            tunnel = ngrok.connect(port)
            public_url = tunnel.public_url
        except Exception as e:
            print(f"Note: ngrok tunnel could not be started: {e}", flush=True)

    if public_url:
        os.makedirs('data', exist_ok=True)
        try:
            with open('data/public_url.txt', 'w', encoding='utf-8') as f:
                f.write(public_url.strip())
        except Exception:
            pass
        print("\n=======================================================", flush=True)
        print(f"  LIVE PUBLIC URL:    {public_url}", flush=True)
        print(f"  CHATGPT EVAL URL:   {public_url}/eval", flush=True)
        print("=======================================================\n", flush=True)

    threading.Timer(2, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    try:
        uvicorn.run('app.main:app', host=host, port=port, workers=1, access_log=False)
    finally:
        if cf_proc:
            try:
                cf_proc.terminate()
            except Exception:
                pass
        if token:
            try:
                from pyngrok import ngrok
                ngrok.kill()
            except Exception:
                pass
