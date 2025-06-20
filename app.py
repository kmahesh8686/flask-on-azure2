from flask import Flask, request, Response
from flask_cors import CORS
import threading
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app)

# 🔐 Proxy Configuration
PROXY_USER = "boss252proxy101"
PROXY_PASS = "EXgckfla"
PROXY_IP_PORT = "43.249.188.102:8000"
PROXY = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP_PORT}"

# Shared response store
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

    # Launch 5 concurrent requests
    threads = []
    for _ in range(5):
        t = threading.Thread(target=send_through_proxy, args=(target_url,))
        t.start()
        threads.append(t)

    # Wait for one to complete with 200 OK
    start_time = time.time()
    while time.time() - start_time < 15:
        with lock:
            if stored_response["content"] is not None:
                break
        time.sleep(0.2)

    for t in threads:
        t.join(timeout=0.1)

    if stored_response["content"]:
        soup = BeautifulSoup(stored_response["content"], 'html.parser')

        # ✅ Insert <base href="..."> for correct relative URL resolution in JS/forms
        head = soup.find("head")
        if head:
            base_tag = soup.new_tag("base", href=target_url)
            head.insert(0, base_tag)

        # ✅ Rewrite all relative resource links
        rewrite_tags = {
            'a': 'href',
            'link': 'href',
            'script': 'src',
            'img': 'src',
            'iframe': 'src',
            'form': 'action',
            'source': 'src',
            'video': 'src',
            'audio': 'src',
            'embed': 'src',
            'input': 'src',
            'track': 'src',
            'object': 'data'
        }

        for tag, attr in rewrite_tags.items():
            for node in soup.find_all(tag):
                if node.has_attr(attr):
                    node[attr] = urljoin(target_url, node[attr])

        html = str(soup)
        return Response(html, status=200, content_type="text/html")

    return "No 200 OK response received", 502

# 🔁 Required for Azure App Service
app = app
