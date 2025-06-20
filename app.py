from flask import Flask, request, Response
from flask_cors import CORS
import threading
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

app = Flask(__name__)
CORS(app)

# 🔐 Proxy Configuration
PROXY_USER = "boss252proxy101"
PROXY_PASS = "EXgckfla"
PROXY_IP_PORT = "43.249.188.102:8000"
PROXY = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP_PORT}"

# Lock-protected no-cache result container
lock = threading.Lock()

def is_same_domain(url, base):
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except:
        return False

def send_through_proxy(target_url, result_container):
    try:
        response = requests.get(
            target_url,
            proxies={"http": PROXY, "https": PROXY},
            headers={"Connection": "close"},
            timeout=10
        )
        if response.status_code == 200:
            with lock:
                if result_container["content"] is None:
                    result_container["content"] = response.text
    except Exception:
        pass

@app.route('/fetch', methods=['GET'])
def fetch_from_proxy():
    target_url = request.args.get('url')
    if not target_url:
        return "Missing ?url= parameter", 400

    result_container = {"content": None}
    threads = []
    for _ in range(5):
        t = threading.Thread(target=send_through_proxy, args=(target_url, result_container))
        t.start()
        threads.append(t)

    start_time = time.time()
    while time.time() - start_time < 15:
        with lock:
            if result_container["content"] is not None:
                break
        time.sleep(0.2)

    for t in threads:
        t.join(timeout=0.1)

    if result_container["content"]:
        soup = BeautifulSoup(result_container["content"], 'html.parser')

        # ✅ Insert <base href="...">
        head = soup.find("head")
        if head:
            base_tag = soup.new_tag("base", href=target_url)
            head.insert(0, base_tag)

        # ✅ Rewrite relative resources only (leave full URLs to original domain untouched)
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
                    old_url = node[attr]
                    if not old_url.lower().startswith("http") or not is_same_domain(old_url, target_url):
                        node[attr] = urljoin(target_url, old_url)

        # ✅ Inject JavaScript to force all AJAX and form submissions to original domain
        inject_script = soup.new_tag("script")
        inject_script.string = f"""
        (function() {{
          const origin = "{target_url}";
          document.querySelectorAll("form").forEach(f => {{
            if (!f.action || f.action.startsWith(window.location.origin)) {{
              f.action = origin;
            }}
          }});

          const originalFetch = window.fetch;
          window.fetch = function(url, ...args) {{
            if (typeof url === "string" && url.startsWith("/")) {{
              return originalFetch(origin + url, ...args);
            }}
            return originalFetch(url, ...args);
          }};

          const origOpen = XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
            if (url.startsWith("/")) {{
              arguments[1] = origin + url;
            }}
            return origOpen.apply(this, arguments);
          }};
        }})();
        """
        soup.body.append(inject_script)

        html = str(soup)
        return Response(html, status=200, content_type="text/html")

    return "No 200 OK response received", 502

# 🔁 For Azure gunicorn
app = app
