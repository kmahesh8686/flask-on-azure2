from flask import Flask, request, Response
from flask_cors import CORS
import threading
import requests
import time

app = Flask(__name__)
CORS(app)

PROXY_USER = "boss252proxy111"
PROXY_PASS = "K3QIDSYA"
PROXY_IP_PORT = "43.249.188.112.ip:8000"
PROXY = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP_PORT}"

stored_response = {"content": None, "content_type": None}
lock = threading.Lock()

def send_through_proxy(target_url):
    global stored_response
    try:
        response = requests.get(
            target_url,
            proxies={"http": PROXY, "https": PROXY},
            headers={"Connection": "close"},
            timeout=10,
            stream=True  # important for binary data
        )
        if response.status_code == 200:
            with lock:
                if stored_response["content"] is None:
                    stored_response["content"] = response.content  # raw binary
                    stored_response["content_type"] = response.headers.get("Content-Type", "application/octet-stream")
    except Exception:
        pass

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
        return Response(stored_response["content"], status=200, content_type=stored_response["content_type"])
    else:
        return "No 200 OK response received", 502

if __name__ == '__main__':
    app.run(debug=True, port=5000)
