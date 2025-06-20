from flask import Flask, request, Response
from flask_cors import CORS
import threading
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app)

# Proxy config
PROXY_USER = "boss252proxy101"
PROXY_PASS = "EXgckfla"
PROXY_IP_PORT = "43.249.188.102:8000"
PROXY = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP_PORT}"

stored_response = {"content": None}
lock = threading.Lock()

def send_through_proxy(target_url):
    global stored_response
    try:
        response = requests.get(
            target_url,
            proxies={"http": PROXY, "https": PROXY},
            headers={"Connection": "close"},
            timeout=10
        )
        if response.status_code == 200:
            with lock:
                if stored_response["content"] is None:
                    stored_response["content"] = response.text
    except Exception:
        pass

@app.route('/fetch', methods=['GET'])
def fetch_from_proxy():
    global stored_response
    stored_response = {"content": None}

    target_url = request.args.get('url')
    if not target_url:
        return "Missing ?url= parameter", 400

    threads = []
    for _ in range(5):
        t = threading.Thread(target=send_through_proxy, args=(target_url,))
        t.start()
        threads.append(t)

    start_time = time.time()
    while time.time() - start_time < 15:
        with lock:
            if stored_response["content"] is not None:
                break
        time.sleep(0.2)

    for t in threads:
        t.join(timeout=0.1)

    if stored_response["content"]:
        # ✅ Rewrite relative links to absolute
        soup = BeautifulSoup(stored_response["content"], 'html.parser')
        for tag in soup.find_all(['a', 'img', 'script', 'link', 'form']):
            attr = 'href' if tag.name in ['a', 'link'] else 'src'
            if tag.has_attr(attr):
                tag[attr] = urljoin(target_url, tag[attr])
            elif tag.name == 'form' and tag.has_attr('action'):
                tag['action'] = urljoin(target_url, tag['action'])

        html = str(soup)
        return Response(html, status=200, content_type="text/html")
    else:
        return "No 200 OK response received", 502

# For Azure
app = app
