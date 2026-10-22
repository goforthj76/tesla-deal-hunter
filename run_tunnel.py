import time
import sys
from pyngrok import ngrok

def start():
    print("Starting permanent ngrok tunnel to port 8765...", flush=True)
    tunnel = ngrok.connect(8765)
    print(f"PERMANENT_TUNNEL_URL: {tunnel.public_url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        ngrok.disconnect(tunnel.public_url)
        ngrok.kill()

if __name__ == '__main__':
    start()
