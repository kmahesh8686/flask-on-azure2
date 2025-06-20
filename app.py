from flask import Flask, request, Response
from flask_cors import CORS
import threading
import requests
import time
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

app = Flask(__name__)
CORS(app)

# 🔐 Proxy configuration
PROXY_USER = "boss252proxy111"
PROXY_PASS = "K3QIDSYA"
PROXY_IP_PORT = "43.249.188.112:8000"
PROXY = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP_PORT}"

# 🧠 Shared response storage
stored_response = {"content": None, "content_type": None}
lock = threading.Lock()

# 🔁 Rewrite relative URLs to full target domain URLs (NOT via /fetch)
def rewrite_urls(base_url, html_content):
    soup = BeautifulSoup(html_content, "html.parser")

    tags_attrs = {
        'a': 'href',
        'img': 'src',
        'link': 'href',
        'script': 'src',
        'iframe': 'src',
        'form': 'action',
        'source': 'src',
        'video': 'src',
        'audio': 'src',
    }

    for tag, attr in tags_attrs.items():
        for element in soup.find_all(tag):
            orig_url = element.get(attr)
            if orig_url and not orig_url.startswith(('data:', 'javascript:', '#', 'http', 'https')):
                full_url = urljoin(base_url, orig_url)
                element[attr] = full_url

    # Inline CSS (e.g., background-image: url(...))
    for tag in soup.find_all(style=True):
        tag['style'] = re.sub(
            r'url\(["\']?(.*?)["\']?\)',
            lambda m: f'url({urljoin(base_url, m.group(1))})'
            if not m.group(1).startswith(('data:', 'http')) else m.group(0),
            tag['style']
        )

    return str(soup)

# 🚀 Thread worker to fetch through proxy
def send_through_proxy(target_url):
    global stored_response
    try:
        response = requests.get(
            target_url,
            proxies={"http": PROXY, "https": PROXY},
            headers={"Connection": "close"},
            timeout=10,
            stream=True
        )
        if response.status_code == 200:
            with lock:
                if stored_response["content"] is None:
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" in content_type:
                        html = response.text
                        rewritten_html = rewrite_urls(target_url, html)
                        stored_response["content"] = rewritten_html.encode("utf-8")
                    else:
                        stored_response["content"] = response.content
                    stored_response["content_type"] = content_type or "application/octet-stream"
    except Exception as e:
        print(f"[ERROR] {e}")

# 🌐 Flask route: /fetch?url=https://target.site
@app.route('/fetch', methods=['GET'])
def fetch_from_proxy():
    global stored_response
    stored_response = {"content": None, "content_type": None}

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
        return Response(
            stored_response["content"],
            status=200,
            content_type=stored_response["content_type"]
        )
    else:
        return "No 200 OK response received", 502

# 🏁 Start server
if __name__ == '__main__':
    app.run(debug=True, port=5000)
