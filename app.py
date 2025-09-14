from flask import Flask, request, jsonify, redirect, url_for, session, render_template_string
from flask_cors import CORS
from datetime import datetime
import zoneinfo, time

app = Flask(__name__)
app.secret_key = "SUPERSECRETKEY"   # change this in production
CORS(app)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# =========================
# Predefined Tokens & Passwords
# =========================
PREDEFINED_TOKENS = ["km8686", "kmk8686", "km5630"]
token_passwords = {t: "12345678" for t in PREDEFINED_TOKENS}

# Admin password
ADMIN_PASSWORD = "12345678"

# Mobile caps per token (None = unlimited)
token_mobile_caps = {t: None for t in PREDEFINED_TOKENS}
token_processed_mobiles = {t: set() for t in PREDEFINED_TOKENS}

# =========================
# Storage per token (in-memory)
# =========================
mobile_otps = {t: [] for t in PREDEFINED_TOKENS}
vehicle_otps = {t: [] for t in PREDEFINED_TOKENS}
otp_data = {t: [] for t in PREDEFINED_TOKENS}         # all delivered/removed OTPs with reasons
client_sessions = {t: {} for t in PREDEFINED_TOKENS}
browser_queues = {t: {} for t in PREDEFINED_TOKENS}
login_sessions = {t: {} for t in PREDEFINED_TOKENS}

BROWSER_STALE_SECONDS = float(10)

# =========================
# Helpers
# =========================
def valid_token(token: str) -> bool:
    return token in PREDEFINED_TOKENS

def add_browser_to_queue(token, identifier, browser_id):
    queues = browser_queues[token]
    sessions = client_sessions[token]
    if identifier not in queues:
        queues[identifier] = []
    if browser_id not in queues[identifier]:
        queues[identifier].append(browser_id)
        sessions[(identifier, browser_id)] = {
            "first_request": datetime.now(IST),
            "last_request": time.time()
        }
    else:
        sessions[(identifier, browser_id)]["last_request"] = time.time()

def get_next_browser(token, identifier):
    queues = browser_queues[token]
    if identifier in queues and queues[identifier]:
        return queues[identifier][0]
    return None

def pop_browser_from_queue(token, identifier):
    queues = browser_queues[token]
    if identifier in queues and queues[identifier]:
        queues[identifier].pop(0)

def mark_otp_removed_to_data(token, entry, reason="stale_browser", browser_id=None):
    record = entry.copy()
    record["removed_at"] = datetime.now(IST)
    record["removed_reason"] = reason
    if browser_id:
        record["browser_id"] = browser_id
    otp_data[token].append(record)

def cleanup_stale_browsers_and_handle_pending(token, identifier):
    now_ts = time.time()
    queues = browser_queues[token]
    sessions = client_sessions[token]
    if identifier not in queues:
        return
    queue_snapshot = list(queues[identifier])
    for b in queue_snapshot:
        sess = sessions.get((identifier, b))
        if not sess:
            try:
                queues[identifier].remove(b)
            except ValueError:
                pass
            sessions.pop((identifier, b), None)
            continue
        last = sess.get("last_request", 0)
        first_req_dt = sess.get("first_request", datetime.now(IST))
        if now_ts - last > BROWSER_STALE_SECONDS:
            try:
                queues[identifier].remove(b)
            except ValueError:
                pass
            sessions.pop((identifier, b), None)

            for p in list(mobile_otps[token]):
                if (p.get("sim_number") or "").upper() == identifier.upper() and p["timestamp"] > first_req_dt:
                    try:
                        mobile_otps[token].remove(p)
                    except ValueError:
                        pass
                    mark_otp_removed_to_data(token, p, reason="stale_browser", browser_id=b)
            for p in list(vehicle_otps[token]):
                if (p.get("vehicle") or "").upper() == identifier.upper() and p["timestamp"] > first_req_dt:
                    try:
                        vehicle_otps[token].remove(p)
                    except ValueError:
                        pass
                    mark_otp_removed_to_data(token, p, reason="stale_browser", browser_id=b)

# =========================
# API Endpoints (clients)
# =========================
@app.route('/api/receive-otp', methods=['POST'])
def receive_otp():
    try:
        data = request.get_json(force=True)
        otp = (data.get('otp') or "").strip()
        token = (data.get('token') or "").strip()
        sim_number = (data.get('sim_number') or "").strip().upper()
        vehicle = (data.get('vehicle') or "").strip().upper()

        if not otp or not token:
            return jsonify({"status": "error", "message": "OTP and token required"}), 400
        if not valid_token(token):
            return jsonify({"status": "error", "message": "Invalid token"}), 403

        # Enforce mobile cap only for mobiles (not vehicles)
        if not vehicle:
            if sim_number not in token_processed_mobiles[token]:
                cap = token_mobile_caps[token]
                if cap is not None and len(token_processed_mobiles[token]) >= cap:
                    entry = {
                        "otp": otp,
                        "token": token,
                        "sim_number": sim_number,
                        "timestamp": datetime.now(IST),
                        "removed_reason": "limit_exceeded"
                    }
                    otp_data[token].append(entry)
                    return jsonify({"status": "success", "message": "OTP stored"}), 200
                token_processed_mobiles[token].add(sim_number)

        entry = {"otp": otp, "token": token, "timestamp": datetime.now(IST)}
        if vehicle:
            entry["vehicle"] = vehicle
            vehicle_otps[token].append(entry)
        else:
            entry["sim_number"] = sim_number or "UNKNOWNSIM"
            mobile_otps[token].append(entry)

        return jsonify({"status": "success", "message": "OTP stored"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    token = (request.args.get('token') or "").strip()
    sim_number = (request.args.get('sim_number') or "").strip().upper()
    vehicle = (request.args.get('vehicle') or "").strip().upper()
    browser_id = (request.args.get('browser_id') or "").strip()

    if not token or (not sim_number and not vehicle) or not browser_id:
        return jsonify({"status": "error", "message": "token + sim_number/vehicle + browser_id required"}), 400
    if not valid_token(token):
        return jsonify({"status": "error", "message": "Invalid token"}), 403

    identifier = sim_number if sim_number else vehicle
    add_browser_to_queue(token, identifier, browser_id)
    cs_key = (identifier, browser_id)
    if cs_key in client_sessions[token]:
        client_sessions[token][cs_key]["last_request"] = time.time()

    cleanup_stale_browsers_and_handle_pending(token, identifier)
    session_entry = client_sessions[token].get(cs_key)
    if not session_entry:
        return jsonify({"status": "waiting"}), 200
    session_time = session_entry["first_request"]
    next_browser = get_next_browser(token, identifier)

    if vehicle:
        new_otps = [o for o in vehicle_otps[token] if o["vehicle"] == vehicle and o["timestamp"] > session_time]
        if new_otps and next_browser == browser_id:
            latest = new_otps[0]
            try:
                vehicle_otps[token].remove(latest)
            except ValueError:
                pass
            latest["browser_id"] = browser_id
            otp_data[token].append(latest)
            pop_browser_from_queue(token, identifier)
            client_sessions[token].pop(cs_key, None)
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "vehicle": latest["vehicle"],
                "browser_id": browser_id,
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        return jsonify({"status": "waiting"}), 200
    else:
        # If sim was blocked by limit
        exceeded = [o for o in otp_data[token] if o.get("sim_number") == sim_number and o.get("removed_reason") == "limit_exceeded"]
        if exceeded:
            return jsonify({"status": "error", "message": "limit_exceeded"}), 403

        new_otps = [o for o in mobile_otps[token] if o["sim_number"] == sim_number and o["timestamp"] > session_time]
        if new_otps and next_browser == browser_id:
            latest = new_otps[0]
            try:
                mobile_otps[token].remove(latest)
            except ValueError:
                pass
            latest["browser_id"] = browser_id
            otp_data[token].append(latest)
            pop_browser_from_queue(token, identifier)
            client_sessions[token].pop(cs_key, None)
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "sim_number": latest["sim_number"],
                "browser_id": browser_id,
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        return jsonify({"status": "waiting"}), 200


@app.route('/api/login-detect', methods=['POST'])
def login_detect():
    try:
        data = request.get_json(force=True)
        token = (data.get('token') or "").strip()
        mobile_number = (data.get('mobile_number') or "").strip().upper()
        source = (data.get('source') or "").strip().upper()

        if not mobile_number or not token:
            return jsonify({"status": "error", "message": "mobile_number and token required"}), 400
        if not valid_token(token):
            return jsonify({"status": "error", "message": "Invalid token"}), 403

        entry = {"timestamp": datetime.now(IST), "source": source}
        login_sessions[token].setdefault(mobile_number, []).append(entry)
        return jsonify({"status": "success", "message": "Login detected"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/login-found', methods=['GET'])
def login_found():
    token = (request.args.get('token') or "").strip()
    mobile_number = (request.args.get('mobile_number') or "").strip().upper()
    if not token or not mobile_number:
        return jsonify({"status": "error", "message": "token + mobile_number required"}), 400
    if not valid_token(token):
        return jsonify({"status": "error", "message": "Invalid token"}), 403

    if mobile_number in login_sessions[token]:
        detections = [
            {"timestamp": e["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), "source": e.get("source","")}
            for e in login_sessions[token][mobile_number]
        ]
        return jsonify({"status": "found", "mobile_number": mobile_number, "detections": detections}), 200
    else:
        return jsonify({"status": "not_found", "mobile_number": mobile_number}), 200

# =========================
# Admin Login + Logout
# =========================
admin_login_page = """
<html><head><title>Admin Login</title></head>
<body style="font-family:Segoe UI, Arial, sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#f4f6f9;">
<div style="background:white;padding:30px;border-radius:10px;box-shadow:0px 8px 30px rgba(0,0,0,0.06);width:380px;">
<h1 style="text-align:center;color:#2980B9;margin:0;font-size:28px;">ADMIN</h1>
<p style="text-align:center;color:#2980B9;margin:6px 0 18px;">Log In To Dashboard</p>
{% if error %}<p style="color:red;text-align:center">{{error}}</p>{% endif %}
<form method="POST">
<label style="font-size:13px;color:#333;">Username</label>
<input type="text" name="username" placeholder="Enter username" required style="width:100%;padding:10px;margin:6px 0 12px;border-radius:6px;border:1px solid #ddd;">
<label style="font-size:13px;color:#333;">Password</label>
<input type="password" name="password" placeholder="Enter password" required style="width:100%;padding:10px;margin:6px 0 12px;border-radius:6px;border:1px solid #ddd;">
<button style="width:100%;padding:12px;background:#2980B9;color:white;border:none;border-radius:6px;font-weight:600;">Login</button>
</form>
</div></body></html>
"""

@app.route('/admin-login', methods=['GET', 'POST'])
def admin_login():
    global ADMIN_PASSWORD
    if request.method == 'POST':
        user = (request.form.get("username") or "").strip().upper()
        pwd = (request.form.get("password") or "").strip()
        if user != "ADMIN":
            return render_template_string(admin_login_page, error="Wrong username")
        if pwd != ADMIN_PASSWORD:
            return render_template_string(admin_login_page, error="Wrong password")
        session["is_admin"] = True
        return redirect(url_for("admin"))
    return render_template_string(admin_login_page, error=None)

@app.route('/admin-logout')
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

# =========================
# Helper to render token sections (partials)
# =========================
def render_token_section_partial(token, section):
    if section == "otp":
        rows = ""
        for i, e in enumerate(otp_data[token]):
            ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
            rows += f"<tr><td><input type='checkbox' name='otp_rows' value='{i}'></td><td>{e.get('sim_number','')}</td><td>{e.get('vehicle','')}</td><td>{e.get('otp','')}</td><td>{e.get('browser_id','')}</td><td>{ts}</td><td>{e.get('removed_reason','')}</td></tr>"
        return f"""
        <div class="card">
            <h3>OTP Data - {token}</h3>
            <form method="POST" action="/status/{token}?embed=1&section=otp">
            <table style="width:100%;border-collapse:collapse;">
                <tr style="background:#2980B9;color:white;"><th>Select</th><th>Mobile</th><th>Vehicle</th><th>OTP</th><th>Browser</th><th>Date</th><th>Reason</th></tr>
                {rows if rows else '<tr><td colspan="7" style="padding:12px">No OTPs found</td></tr>'}
            </table>
            <div style="margin-top:10px;">
                <button type="submit" name="delete_selected_otps" style="padding:8px 10px;background:#e67e22;color:white;border:none;border-radius:6px;">Delete Selected</button>
                <button type="submit" name="delete_all_otps" style="padding:8px 10px;background:#c0392b;color:white;border:none;border-radius:6px;margin-left:8px;">Delete All</button>
            </div>
            </form>
        </div>"""

    if section == "login":
        rows = ""
        for m, entries in login_sessions[token].items():
            for i, e in enumerate(entries):
                rows += f"<tr><td><input type='checkbox' name='login_rows' value='{m}:{i}'></td><td>{m}</td><td>{e['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td><td>{e.get('source','')}</td></tr>"
        return f"""
        <div class="card">
            <h3>Login Detections - {token}</h3>
            <form method="POST" action="/status/{token}?embed=1&section=login">
            <table style="width:100%;border-collapse:collapse;">
                <tr style="background:#2980B9;color:white;"><th>Select</th><th>Mobile</th><th>Date</th><th>Source</th></tr>
                {rows if rows else '<tr><td colspan="4" style="padding:12px">No login detections</td></tr>'}
            </table>
            <div style="margin-top:10px;">
                <button type="submit" name="delete_selected_logins" style="padding:8px 10px;background:#e67e22;color:white;border:none;border-radius:6px;">Delete Selected</button>
                <button type="submit" name="delete_all_logins" style="padding:8px 10px;background:#c0392b;color:white;border:none;border-radius:6px;margin-left:8px;">Delete All</button>
            </div>
            </form>
        </div>"""

    if section == "change_password":
        return f"""
        <div class="card">
            <h3>Change Token Password - {token}</h3>
            <form method="POST" action="/change-password/{token}">
                <label>Current Password</label><input type="password" name="current_password" required style="padding:8px;margin-top:6px;width:100%;border-radius:6px;border:1px solid #ddd;">
                <label style="margin-top:8px">New Password</label><input type="password" name="new_password" required style="padding:8px;margin-top:6px;width:100%;border-radius:6px;border:1px solid #ddd;">
                <label style="margin-top:8px">Confirm Password</label><input type="password" name="confirm_password" required style="padding:8px;margin-top:6px;width:100%;border-radius:6px;border:1px solid #ddd;">
                <div style="margin-top:10px;"><button type="submit" style="background:#27AE60;color:white;padding:8px 12px;border:none;border-radius:6px;">Change</button></div>
            </form>
        </div>"""

    return "<div class='card'><p>Invalid section</p></div>"

# =========================
# Admin Dashboard + Sections
# =========================
@app.route('/admin')
def admin():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))

    tokens_js_list = "[" + ",".join([f"'{t}'" for t in PREDEFINED_TOKENS]) + "]"

    html = f"""
    <html>
    <head>
        <title>Admin Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', Arial; margin:0; background:#f4f6f9; }}
            .app {{ display:flex; min-height:100vh; }}
            .sidebar {{ width:260px; background:#2C3E50; color:white; padding:22px; display:flex; flex-direction:column; }}
            .sidebar h2 {{ margin:0 0 14px; font-size:20px; text-align:center; }}
            .sidebar a {{ display:block; padding:10px; margin-bottom:8px; color:#fff; text-decoration:none; border-radius:6px; background:rgba(255,255,255,0.05); }}
            .main {{ flex-grow:1; padding:24px; }}
            .card {{ background:white; padding:16px; border-radius:8px; box-shadow:0px 4px 18px rgba(0,0,0,0.06); margin-bottom:20px; }}
            .token-tile {{ padding:12px;background:#ecf0f1;border-radius:6px;width:160px;text-align:center;cursor:pointer;font-weight:700;color:#2C3E50; }}
        </style>
        <script>
            function loadTokens() {{
                var tokens = {tokens_js_list};
                var html = "<div class='card'><h3>Tokens</h3><div style='display:flex;gap:12px;flex-wrap:wrap'>";
                tokens.forEach(function(t) {{
                    html += "<div class='token-tile' onclick=\\"loadTokenFull('" + t + "')\\">" + t + "</div>";
                }});
                html += "</div></div>";
                document.getElementById('content_panel').innerHTML = html;
            }}
            function loadTokenFull(token) {{
                document.getElementById('content_panel').innerHTML = "<div class='card'><p>Loading token...</p></div>";
                fetch('/status/' + token + '?embed=admin_full', {{ credentials: 'same-origin' }})
                .then(r=>r.text()).then(h=>document.getElementById('content_panel').innerHTML=h);
            }}
            function loadLimit() {{
                var tokens = {tokens_js_list};
                var html = "<div class='card'><h3>Limit Exceeded</h3><div style='display:flex;gap:12px;flex-wrap:wrap'>";
                tokens.forEach(function(t) {{
                    html += "<div class='token-tile' onclick=\\"loadLimitToken('" + t + "')\\">" + t + "</div>";
                }});
                html += "</div></div>";
                document.getElementById('content_panel').innerHTML = html;
            }}
            function loadLimitToken(token) {{
                document.getElementById('content_panel').innerHTML = "<div class='card'><p>Loading...</p></div>";
                fetch('/admin/limit/' + token + '?embed=1').then(r=>r.text()).then(h=>document.getElementById('content_panel').innerHTML=h);
            }}
            function loadCaps() {{
                document.getElementById('content_panel').innerHTML = "<div class='card'><p>Loading caps...</p></div>";
                fetch('/admin/caps?embed=1').then(r=>r.text()).then(h=>document.getElementById('content_panel').innerHTML=h);
            }}
            function updateServerTime() {{
                var now = new Date();
                document.getElementById('server_time').innerText = now.toLocaleString();
            }}
            setInterval(updateServerTime,1000);
            window.onload = function() {{
                updateServerTime();
                document.getElementById('content_panel').innerHTML = "<div class='card'><h3>Welcome, Admin</h3></div>";
            }};
        </script>
    </head>
    <body>
        <div class="app">
            <div class="sidebar">
                <h2>ADMIN</h2>
                <a href="#" onclick="loadTokens()">TOKENS</a>
                <a href="#" onclick="loadLimit()">LIMIT EXCEEDED</a>
                <a href="#" onclick="loadCaps()">TOKEN CAPS</a>
                <a href="/admin-logout" style="background:#E74C3C;">LOGOUT</a>
                <div style="margin-top:auto;font-size:12px;">Server time: <span id="server_time"></span></div>
            </div>
            <div class="main">
                <div id="content_panel" class="card"></div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# =========================
# Admin: limit view + delete
# =========================
@app.route('/admin/limit/<token>', methods=['GET','POST'])
def admin_limit(token):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if token not in PREDEFINED_TOKENS:
        return "Invalid token", 404

    if request.method == 'POST':
        if "delete_selected" in request.form:
            to_delete = [int(x) for x in request.form.getlist("otp_rows")]
            otp_data[token] = [e for i, e in enumerate(otp_data[token]) if not (i in to_delete and e.get("removed_reason")=="limit_exceeded")]
        elif "delete_all" in request.form:
            otp_data[token] = [e for e in otp_data[token] if e.get("removed_reason")!="limit_exceeded"]

    rows = ""
    for i, e in enumerate(otp_data[token]):
        if e.get("removed_reason") == "limit_exceeded":
            ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
            rows += f"<tr><td><input type='checkbox' name='otp_rows' value='{i}'></td><td>{e.get('sim_number','')}</td><td>{e.get('otp','')}</td><td>{ts}</td></tr>"

    if request.args.get("embed") == "1":
        return f"""
        <div class="card">
            <h3>Limit Exceeded - {token}</h3>
            <form method="POST" action="/admin/limit/{token}?embed=1">
                <table style="width:100%;border-collapse:collapse;">
                    <tr style="background:#E74C3C;color:white;"><th>Select</th><th>Mobile</th><th>OTP</th><th>Date</th></tr>
                    {rows if rows else '<tr><td colspan="4" style="padding:12px">No limit-exceeded OTPs</td></tr>'}
                </table>
                <div style="margin-top:10px;">
                    <button type="submit" name="delete_selected" style="background:#e67e22;color:white;padding:8px 12px;border:none;border-radius:6px;">Delete Selected</button>
                    <button type="submit" name="delete_all" style="background:#c0392b;color:white;padding:8px 12px;border:none;border-radius:6px;margin-left:6px;">Delete All</button>
                </div>
            </form>
        </div>
        """
    return f"<html><body><pre>Limit Exceeded for {token}</pre></body></html>"

# =========================
# Admin: caps view
# =========================
@app.route('/admin/caps', methods=['GET','POST'])
def admin_caps():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        t = request.form.get("token")
        cap = request.form.get("cap")
        if t in PREDEFINED_TOKENS:
            token_mobile_caps[t] = int(cap) if cap else None

    if request.args.get("embed") == "1":
        rows = ""
        for idx, t in enumerate(PREDEFINED_TOKENS):
            rows += f"""
            <tr>
                <td>{t}</td>
                <td style='text-align:center'>{len(token_processed_mobiles[t])}</td>
                <td style='text-align:center'>{token_mobile_caps[t] if token_mobile_caps[t] is not None else 'Unlimited'}</td>
                <td>
                    <form method="POST" action="/admin/caps?embed=1" style="display:inline-block">
                        <input type="hidden" name="token" value="{t}">
                        <input type="number" name="cap" placeholder="Enter cap" style="width:80px;padding:4px;">
                        <button type="submit" style="background:#2980B9;color:white;padding:6px 8px;border:none;border-radius:4px;">Set</button>
                    </form>
                    <button onclick="showProcessedMobiles('{t}', {idx})" style="background:#2ecc71;color:white;padding:6px 8px;border:none;border-radius:4px;">Processed</button>
                </td>
            </tr>
            <tr id="processed_placeholder_{idx}"><td colspan="4"></td></tr>
            """
        return f"""
        <div class="card">
            <h3>Token Mobile Caps</h3>
            <table style="width:100%;border-collapse:collapse;">
                <tr style="background:#2980B9;color:white;">
                    <th>Token</th><th>Processed</th><th>Cap</th><th>Action</th>
                </tr>
                {rows}
            </table>
        </div>
        """
    return redirect(url_for("admin"))

@app.route('/admin/processed/<token>')
def admin_processed(token):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if token not in PREDEFINED_TOKENS:
        return "Invalid token", 404
    rows = ""
    for m in sorted(token_processed_mobiles[token]):
        related = [e for e in otp_data[token] if e.get("sim_number")==m]
        if related:
            for e in related:
                ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
                rows += f"<tr><td>{m}</td><td>{e.get('otp','')}</td><td>{e.get('removed_reason','')}</td><td>{ts}</td></tr>"
        else:
            rows += f"<tr><td>{m}</td><td></td><td></td><td></td></tr>"
    return f"""
    <div style="padding:12px;">
        <h4>Processed mobiles - {token}</h4>
        <table style="width:100%;border-collapse:collapse;">
            <tr style="background:#2980B9;color:white;"><th>Mobile</th><th>OTP</th><th>Reason</th><th>Date</th></tr>
            {rows if rows else '<tr><td colspan="4">No processed</td></tr>'}
        </table>
    </div>
    """

# =========================
# Token Login + Logout
# =========================
login_page_html = """
<html><head><title>Login</title></head>
<body style="font-family:Segoe UI;display:flex;justify-content:center;align-items:center;height:100vh;background:#f4f6f9;">
<div style="background:white;padding:30px;border-radius:10px;box-shadow:0px 8px 30px rgba(0,0,0,0.06);width:380px;">
<h1 style="text-align:center;color:#2980B9;margin:0;font-size:28px;">KM OTP</h1>
<p style="text-align:center;color:#2980B9;margin:6px 0 18px;">Log In To Your Account</p>
{% if error %}<p style="color:red;text-align:center">{{error}}</p>{% endif %}
<form method="POST">
<label style="font-weight:700;color:#333;">Token</label>
<input type="text" name="token" required style="width:100%;padding:10px;margin:6px 0 12px;border-radius:6px;border:1px solid #ddd;">
<label style="font-weight:700;color:#333;">Password</label>
<input type="password" name="password" required style="width:100%;padding:10px;margin:6px 0 12px;border-radius:6px;border:1px solid #ddd;">
<button style="width:100%;padding:12px;background:#2980B9;color:white;border:none;border-radius:6px;font-weight:700;">Login</button>
</form>
</div></body></html>
"""

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        token = (request.form.get("token") or "").strip()
        pwd = (request.form.get("password") or "").strip()
        if token not in PREDEFINED_TOKENS:
            return render_template_string(login_page_html, error="Wrong token")
        if pwd != token_passwords[token]:
            return render_template_string(login_page_html, error="Wrong password")
        session["token"] = token
        return redirect(url_for("status", token=token))
    return render_template_string(login_page_html, error=None)

@app.route('/logout')
def logout():
    session.pop("token", None)
    return redirect(url_for("login"))

# =========================
# Token Dashboard
# =========================
@app.route('/status/<token>', methods=['GET','POST'])
def status(token):
    if not (("token" in session and session["token"]==token) or session.get("is_admin")):
        return redirect(url_for("login"))

    if request.method == "POST":
        if "delete_selected_otps" in request.form:
            to_delete = [int(x) for x in request.form.getlist("otp_rows")]
            otp_data[token] = [e for i,e in enumerate(otp_data[token]) if i not in to_delete]
        elif "delete_all_otps" in request.form:
            otp_data[token].clear()
        elif "delete_selected_logins" in request.form:
            to_delete = request.form.getlist("login_rows")
            for x in to_delete:
                m, idx = x.split(":"); idx=int(idx)
                if m in login_sessions[token] and 0<=idx<len(login_sessions[token][m]):
                    login_sessions[token][m].pop(idx)
                    if not login_sessions[token][m]: login_sessions[token].pop(m)
        elif "delete_all_logins" in request.form:
            login_sessions[token].clear()

    if request.args.get("embed")=="1":
        return render_token_section_partial(token, request.args.get("section","otp"))

    if request.args.get("embed")=="admin_full":
        return f"""
        <div style="display:flex;gap:18px;">
            <div style="width:220px;background:#2C3E50;color:white;padding:12px;border-radius:6px;">
                <h3 style="text-align:center;">{token}</h3>
                <button onclick="fetch('/status/{token}?embed=1&section=otp').then(r=>r.text()).then(h=>document.getElementById('token_right_panel').innerHTML=h)">OTP DATA</button>
                <button onclick="fetch('/status/{token}?embed=1&section=login').then(r=>r.text()).then(h=>document.getElementById('token_right_panel').innerHTML=h)">LOGIN DETECTIONS</button>
                <button onclick="fetch('/status/{token}?embed=1&section=change_password').then(r=>r.text()).then(h=>document.getElementById('token_right_panel').innerHTML=h)">CHANGE PASSWORD</button>
            </div>
            <div id="token_right_panel" style="flex:1;">
                {render_token_section_partial(token,'otp')}
            </div>
        </div>
        """

    return f"""
    <html><head><title>{token} Dashboard</title></head>
    <body style="font-family:Segoe UI;background:#f9f9f9;margin:0;">
    <h2 style="text-align:center;">Dashboard ({token})</h2>
    <div style="display:flex;">
        <div style="width:220px;background:#2C3E50;color:white;padding:20px;">
            <button onclick="window.location='?embed=1&section=otp'">OTP DATA</button>
            <button onclick="window.location='?embed=1&section=login'">LOGIN DETECTIONS</button>
            <button onclick="window.location='?embed=1&section=change_password'">CHANGE PASSWORD</button>
            <a href="/logout" style="color:white;">LOGOUT</a>
        </div>
        <div style="flex:1;padding:20px;" id="right_panel">{render_token_section_partial(token,'otp')}</div>
    </div>
    </body></html>
    """

# =========================
# Run App
# =========================
if __name__ == "__main__":
    app.run(debug=True)
