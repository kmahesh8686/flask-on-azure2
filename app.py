from flask import Flask, request, Response, send_file
from flask_cors import CORS
import threading
import requests
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import os

app = Flask(__name__)
CORS(app)

PROXY_USER = "boss252proxy101"
PROXY_PASS = "EXgckfla"
PROXY_IP_PORT = "43.249.188.102:8000"
PROXY = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP_PORT}"

lock = threading.Lock()

def is_same_domain(url, base):
    try:
        return urlparse(url).netloc == urlparse(base).netloc
    except:
        return False

def send_through_proxy(target_url, result_container):
    try:
        session = requests.Session()
        response = session.get(
            target_url,
            proxies={"http": PROXY, "https": PROXY},
            headers={"Connection": "close"},
            timeout=10
        )
        if response.status_code == 200:
            with lock:
                if result_container["content"] is None:
                    result_container["content"] = response.text
                    result_container["cookies"] = session.cookies.get_dict()
    except Exception:
        pass

@app.route('/WebResource.axd')
def serve_webresource_script():
    return send_file("webresource_script.js", mimetype="application/javascript")

@app.route('/fetch', methods=['GET'])
def fetch_from_proxy():
    target_url = request.args.get('url')
    if not target_url:
        return "Missing ?url= parameter", 400

    result_container = {"content": None, "cookies": {}}
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

        # ✅ Add <base href="...">
        head = soup.find("head")
        if head:
            base_tag = soup.new_tag("base", href=target_url)
            head.insert(0, base_tag)

        # 🚫 Remove external WebResource.axd
        for script_tag in soup.find_all("script", src=True):
            if "WebResource.axd" in script_tag["src"]:
                script_tag["src"] = "/WebResource.axd"

        # ✅ Rewrite all relative resource URLs
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

        # ✅ Inject cookies and patch JS
        cookie_js = "; ".join(f"{k}='{v}'" for k, v in result_container["cookies"].items())

        inject_script = soup.new_tag("script")
        inject_script.string = f"""
        (function() {{
            const origin = "{target_url}";
            document.cookie = "{cookie_js}; path=/; domain=.sand.telangana.gov.in";

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

            window.onerror = function(msg, src, line, col, err) {{
                console.log("JS error:", msg, "at", src, line + ":" + col);
            }};
        }})();
        """
        soup.body.append(inject_script)

        return Response(str(soup), status=200, content_type="text/html")

    return "No 200 OK response received", 502

app = app
