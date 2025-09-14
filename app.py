from flask import Flask, request, jsonify, redirect, url_for, session, render_template_string
from flask_cors import CORS
from datetime import datetime
import zoneinfo, time

app = Flask(__name__)
app.secret_key = "SUPERSECRETKEY"   # 🔒 change this in production
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
# APIs (open for clients)
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
                    # Store directly to otp_data with reason limit_exceeded
                    entry = {
                        "otp": otp,
                        "token": token,
                        "sim_number": sim_number,
                        "timestamp": datetime.now(IST),
                        "removed_reason": "limit_exceeded"
                    }
                    otp_data[token].append(entry)
                    # App always sees success
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
# Admin Login + Dashboard (full modern UI)
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

@app.route('/admin-login', methods=['GET','POST'])
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

# Partial renderer for token embed (used by admin)
def render_token_section_partial(token, section):
    # OTP section
    if section == "otp":
        rows = ""
        for i, e in enumerate(otp_data[token]):
            ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
            rows += f"<tr><td><input type='checkbox' name='otp_rows' value='{i}'></td><td>{e.get('sim_number','')}</td><td>{e.get('vehicle','')}</td><td>{e.get('otp','')}</td><td>{e.get('browser_id','')}</td><td>{ts}</td><td>{e.get('removed_reason','')}</td></tr>"
        partial = f"""
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
        </div>
        """
        return partial

    # Login section
    if section == "login":
        rows = ""
        for m, entries in login_sessions[token].items():
            for i, e in enumerate(entries):
                rows += f"<tr><td><input type='checkbox' name='login_rows' value='{m}:{i}'></td><td>{m}</td><td>{e['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td><td>{e.get('source','')}</td></tr>"
        partial = f"""
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
        </div>
        """
        return partial

    # Change password for token (admin can present this, but action is via admin/change-token-password)
    if section == "change_password":
        partial = f"""
        <div class="card">
            <h3>Change Token Password - {token}</h3>
            <form method="POST" action="/admin/change-token-password/{token}">
                <label>New Password</label><input type="password" name="new_password" required style="padding:8px;margin-top:6px;width:100%;border-radius:6px;border:1px solid #ddd;">
                <label style="margin-top:8px">Confirm Password</label><input type="password" name="confirm_password" required style="padding:8px;margin-top:6px;width:100%;border-radius:6px;border:1px solid #ddd;">
                <div style="margin-top:10px;"><button type="submit" style="background:#27AE60;color:white;padding:8px 12px;border:none;border-radius:6px;">Change</button></div>
            </form>
        </div>
        """
        return partial

    return "<div class='card'><p>Invalid section</p></div>"

@app.route('/admin/change-token-password/<token>', methods=['POST'])
def admin_change_token_password(token):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if token not in PREDEFINED_TOKENS:
        return "Invalid token", 404
    new = request.form.get("new_password")
    conf = request.form.get("confirm_password")
    if not new or new != conf:
        return "Passwords do not match", 400
    token_passwords[token] = new
    return f"Password for {token} updated."

@app.route('/admin', methods=['GET'])
def admin():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))

    # token list (used when admin clicks "TOKENS")
    tokens_js_list = "[" + ",".join([f"'{t}'" for t in PREDEFINED_TOKENS]) + "]"

    html = f"""
    <html>
    <head>
        <title>Admin Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; margin:0; background:#f4f6f9; }}
            .app {{ display:flex; min-height:100vh; }}
            .sidebar {{ width:260px; background:#2C3E50; color:white; padding:22px; box-sizing:border-box; display:flex; flex-direction:column; }}
            .sidebar h2 {{ margin:0 0 14px; font-size:20px; text-align:center; color:#fff; }}
            .sidebar .section-title {{ margin-top:12px; font-weight:700; color:#ecf0f1; }}
            .sidebar ul {{ list-style:none; padding:0; margin:8px 0; }}
            .sidebar a.token-link {{ display:block; padding:8px 10px; background:rgba(255,255,255,0.02); border-radius:6px; color:#fff; text-decoration:none; margin-bottom:6px; }}
            .main {{ flex-grow:1; padding:24px; }}
            .card {{ background:white; padding:16px; border-radius:8px; box-shadow:0px 4px 18px rgba(0,0,0,0.06); margin-bottom:20px; }}
            table {{ width:100%; border-collapse:collapse; }}
            table th {{ background:#2980B9; color:white; padding:10px; text-align:left; }}
            table td {{ padding:10px; border-bottom:1px solid #eee; }}
            .muted {{ color:#666; font-size:13px; }}
            button.primary {{ background:#2980B9; color:white; border:none; padding:8px 12px; border-radius:6px; cursor:pointer; }}
            .topbar {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }}
            .token-tile {{ padding:12px;background:#ecf0f1;border-radius:6px;width:160px;text-align:center;cursor:pointer;font-weight:700;color:#2C3E50; }}
            .tokens-grid {{ display:flex; gap:12px; flex-wrap:wrap; }}
        </style>
        <script>
            function loadTokens() {{
                var tokens = {tokens_js_list};
                var html = "<div class='card'><h3>Tokens</h3><div class='tokens-grid'>";
                tokens.forEach(function(t) {{
                    html += "<div class='token-tile' onclick=\\\"loadToken('" + t + "','otp')\\\">" + t + "</div>";
                }});
                html += "</div></div>";
                document.getElementById('content_panel').innerHTML = html;
            }}
            function loadToken(token, section) {{
                var url = '/status/' + token + '?embed=1&section=' + section;
                document.getElementById('content_panel').innerHTML = '<div class="card"><p>Loading...</p></div>';
                fetch(url, {{ credentials: 'same-origin' }})
                    .then(function(r){{ return r.text(); }})
                    .then(function(html){{ document.getElementById('content_panel').innerHTML = html; }})
                    .catch(function(e){{ document.getElementById('content_panel').innerHTML = '<div class="card" style="color:red">Failed to load</div>'; }});
            }}
            function loadLimit(token) {{
                var url = '/admin/limit/' + token + '?embed=1';
                document.getElementById('content_panel').innerHTML = '<div class="card"><p>Loading...</p></div>';
                fetch(url, {{ credentials: 'same-origin' }})
                    .then(function(r){{ return r.text(); }})
                    .then(function(html){{ document.getElementById('content_panel').innerHTML = html; }})
                    .catch(function(e){{ document.getElementById('content_panel').innerHTML = '<div class="card" style="color:red">Failed to load</div>'; }});
            }}
            function loadCaps() {{
                var url = '/admin/caps?embed=1';
                document.getElementById('content_panel').innerHTML = '<div class="card"><p>Loading...</p></div>';
                fetch(url, {{ credentials: 'same-origin' }})
                    .then(function(r){{ return r.text(); }})
                    .then(function(html){{ document.getElementById('content_panel').innerHTML = html; }})
                    .catch(function(e){{ document.getElementById('content_panel').innerHTML = '<div class="card" style="color:red">Failed to load</div>'; }});
            }}
            function loadAdminChangePassword() {{
                var url = '/admin/change-password?embed=1';
                document.getElementById('content_panel').innerHTML = '<div class="card"><p>Loading...</p></div>';
                fetch(url, {{ credentials: 'same-origin' }})
                    .then(function(r){{ return r.text(); }})
                    .then(function(html){{ document.getElementById('content_panel').innerHTML = html; }})
                    .catch(function(e){{ document.getElementById('content_panel').innerHTML = '<div class="card" style="color:red">Failed to load</div>'; }});
            }}
            function loadProcessed(token) {{
                var url = '/admin/processed/' + token + '?embed=1';
                document.getElementById('content_panel').innerHTML = '<div class="card"><p>Loading...</p></div>';
                fetch(url, {{ credentials: 'same-origin' }})
                    .then(function(r){{ return r.text(); }})
                    .then(function(html){{ document.getElementById('content_panel').innerHTML = html; }})
                    .catch(function(e){{ document.getElementById('content_panel').innerHTML = '<div class="card" style="color:red">Failed to load</div>'; }});
            }}
            window.onload = function() {{
                document.getElementById('content_panel').innerHTML = '<div class=\"card\"><h3>Welcome, Admin</h3><p class=\"muted\">Click \"TOKENS\" to view tokens or use other sidebar actions.</p></div>';
            }};
        </script>
    </head>
    <body>
        <div class="app">
            <div class="sidebar">
                <h2>ADMIN</h2>

                <div class="section-title">MENU</div>
                <ul>
                    <li><a href="#" class="token-link" onclick="loadTokens()">TOKENS</a></li>
                    <li><a href="#" class="token-link" onclick="loadLimit('{PREDEFINED_TOKENS[0]}')">LIMIT EXCEEDED</a></li>
                    <li><a href="#" class="token-link" onclick="loadCaps()">TOKEN CAPS</a></li>
                    <li><a href="#" class="token-link" onclick="loadAdminChangePassword()">CHANGE ADMIN PASSWORD</a></li>
                    <li style="margin-top:14px;"><a href="/admin-logout" style="display:inline-block;background:#E74C3C;padding:8px 10px;border-radius:6px;color:#fff;text-decoration:none;">Logout</a></li>
                </ul>
                <div style="margin-top:auto;color:#bdc3c7;font-size:12px;padding-top:12px;">Server time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}</div>
            </div>

            <div class="main">
                <div id="content_panel" class="card">
                    <h3>Welcome, Admin</h3>
                    <p class="muted">Click a token or a section from the left to load fresh data here (all data is fetched each time).</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# =========================
# Admin: limit view for a token (shows only limit-exceeded OTPs)
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
            otp_data[token] = [e for i, e in enumerate(otp_data[token]) if not (i in to_delete and e.get("removed_reason") == "limit_exceeded")]
        elif "delete_all" in request.form:
            otp_data[token] = [e for e in otp_data[token] if e.get("removed_reason") != "limit_exceeded"]
        return redirect(url_for("admin_limit", token=token))

    rows = ""
    for i, e in enumerate(otp_data[token]):
        if e.get("removed_reason") == "limit_exceeded":
            ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
            rows += f"<tr><td><input type='checkbox' name='otp_rows' value='{i}'></td><td>{e.get('sim_number','')}</td><td>{e.get('vehicle','')}</td><td>{e.get('otp','')}</td><td>{e.get('browser_id','')}</td><td>{ts}</td></tr>"

    if request.args.get("embed") == "1":
        partial = f"""
        <div class="card">
            <h3>Limit Exceeded - {token}</h3>
            <form method="POST">
                <table style="width:100%;border-collapse:collapse;">
                    <tr style="background:#E74C3C;color:white;"><th>Select</th><th>Mobile</th><th>Vehicle</th><th>OTP</th><th>Browser</th><th>Date</th></tr>
                    {rows if rows else '<tr><td colspan="6" style="padding:12px">No limit-exceeded OTPs</td></tr>'}
                </table>
                <div style="margin-top:10px;"><button type="submit" name="delete_selected" class="primary">Delete Selected</button> <button type="submit" name="delete_all" class="primary" style="background:#c0392b;">Delete All</button></div>
            </form>
        </div>
        """
        return partial

    return f"<html><body><pre>Limit Exceeded for {token}</pre></body></html>"

# =========================
# Admin: caps partial + processed mobiles partial
# =========================
@app.route('/admin/caps', methods=['GET'])
def admin_caps():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if request.args.get("embed") == "1":
        rows = ""
        for t in PREDEFINED_TOKENS:
            rows += f"<tr><td>{t}</td><td style='text-align:center'>{len(token_processed_mobiles[t])}</td><td style='text-align:center'>{token_mobile_caps[t] if token_mobile_caps[t] is not None else 'Unlimited'}</td>"
            rows += f"<td><form method='POST' action='/admin/update-cap' style='display:inline-block'><input type='hidden' name='token' value='{t}'><input type='number' name='cap' placeholder='Enter cap' style='padding:6px;width:120px;margin-right:6px;'><button type='submit' class='primary'>Set</button></form> <button onclick=\"fetch('/admin/processed/{t}?embed=1').then(r=>r.text()).then(h=>document.getElementById('processed_panel').innerHTML=h)\" class='primary' style='background:#27ae60;margin-left:8px;'>Processed</button></td></tr>"
        partial = f"""
        <div class="card">
            <h3>Token Mobile Caps</h3>
            <table style="width:100%;border-collapse:collapse;">
                <tr style="background:#2980B9;color:white;"><th>Token</th><th>Processed Mobiles</th><th>Cap</th><th>Action</th></tr>
                {rows}
            </table>
            <div id="processed_panel" style="margin-top:12px;"></div>
        </div>
        """
        return partial
    return redirect(url_for("admin"))

@app.route('/admin/update-cap', methods=['POST'])
def admin_update_cap():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    t = request.form.get("token")
    cap = request.form.get("cap")
    if t in PREDEFINED_TOKENS:
        token_mobile_caps[t] = int(cap) if cap else None
        return redirect(url_for("admin_caps", embed=1))
    return "Invalid token", 400

@app.route('/admin/processed/<token>', methods=['GET'])
def admin_processed(token):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if request.args.get("embed") == "1":
        rows = ""
        # Provide a summarized view with timestamp + count of OTPs delivered for that mobile (from otp_data)
        for m in sorted(token_processed_mobiles[token]):
            # count OTP records in otp_data (delivered or removed) with this sim_number
            count = sum(1 for e in otp_data[token] if e.get("sim_number") == m) + sum(1 for e in mobile_otps[token] if e.get("sim_number") == m)
            first_ts_entries = [e for e in otp_data[token] if e.get("sim_number") == m]
            first_ts = first_ts_entries[0].get("timestamp").strftime("%Y-%m-%d %H:%M:%S") if first_ts_entries else ""
            rows += f"<tr><td>{m}</td><td style='text-align:center'>{count}</td><td style='text-align:center'>{first_ts}</td></tr>"
        partial = f"""
        <div class="card">
            <h4>Processed Mobiles - {token}</h4>
            <table style="width:100%;border-collapse:collapse;">
                <tr style="background:#7f8c8d;color:white;"><th>Mobile</th><th>Processed Count</th><th>First Seen</th></tr>
                {rows if rows else '<tr><td colspan="3" style="padding:12px">No processed mobiles yet</td></tr>'}
            </table>
        </div>
        """
        return partial
    return redirect(url_for("admin"))

# =========================
# Admin change password
# =========================
@app.route('/admin/change-password', methods=['GET', 'POST'])
def admin_change_password():
    global ADMIN_PASSWORD
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if request.method == "POST":
        cur = request.form.get("current_password")
        new = request.form.get("new_password")
        conf = request.form.get("confirm_password")
        if cur != ADMIN_PASSWORD:
            return "Current password incorrect", 400
        if new != conf:
            return "Passwords do not match", 400
        ADMIN_PASSWORD = new
        return "Admin password changed."
    if request.args.get("embed") == "1":
        return """
        <div class="card">
            <h3>Change Admin Password</h3>
            <form method="POST" action="/admin/change-password">
                <label>Current Password</label><input type="password" name="current_password" required style="padding:8px;width:100%;border-radius:6px;border:1px solid #ddd;"><br>
                <label>New Password</label><input type="password" name="new_password" required style="padding:8px;width:100%;border-radius:6px;border:1px solid #ddd;"><br>
                <label>Confirm Password</label><input type="password" name="confirm_password" required style="padding:8px;width:100%;border-radius:6px;border:1px solid #ddd;"><br>
                <div style="margin-top:8px;"><button type="submit" class="primary">Change Password</button></div>
            </form>
        </div>
        """
    return redirect(url_for("admin"))

# =========================
# Token login/dashboard
# Supports embed=1 (partial HTML) and admin direct access
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
<input type="text" name="token" placeholder="Enter your token" required style="width:100%;padding:10px;margin:6px 0 12px;border-radius:6px;border:1px solid #ddd;">
<label style="font-weight:700;color:#333;">Password</label>
<input type="password" name="password" placeholder="Enter password" required style="width:100%;padding:10px;margin:6px 0 12px;border-radius:6px;border:1px solid #ddd;">
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

@app.route('/change-password/<token>', methods=['POST'])
def change_password(token):
    # Only token owner may change their password via token dashboard (not admin)
    if "token" not in session or session["token"] != token:
        return redirect(url_for("login"))
    cur = request.form.get("current_password")
    new = request.form.get("new_password")
    confirm = request.form.get("confirm_password")
    if cur != token_passwords[token]:
        return redirect(url_for("status", token=token, err="wrong_current"))
    if new != confirm:
        return redirect(url_for("status", token=token, err="nomatch"))
    token_passwords[token] = new
    return redirect(url_for("status", token=token, msg="changed"))

@app.route('/status/<token>', methods=['GET','POST'])
def status(token):
    # allow admin direct access OR token-login access
    if not (("token" in session and session["token"] == token) or session.get("is_admin")):
        return redirect(url_for("login"))

    # handle deletes when embed=1 (partials) or full page form posts
    if request.method == 'POST':
        # OTP deletes
        if "delete_selected_otps" in request.form:
            to_delete = [int(x) for x in request.form.getlist("otp_rows")]
            otp_data[token] = [e for i, e in enumerate(otp_data[token]) if i not in to_delete]
            if request.args.get('embed') == '1':
                return render_token_section_partial(token, 'otp')
            else:
                return redirect(url_for("status", token=token))
        elif "delete_all_otps" in request.form:
            otp_data[token].clear()
            if request.args.get('embed') == '1':
                return render_token_section_partial(token, 'otp')
            else:
                return redirect(url_for("status", token=token))
        # Login deletes
        elif "delete_selected_logins" in request.form:
            to_delete = request.form.getlist("login_rows")
            for x in to_delete:
                m, idx = x.split(":")
                idx = int(idx)
                if m in login_sessions[token] and 0 <= idx < len(login_sessions[token][m]):
                    login_sessions[token][m].pop(idx)
                    if not login_sessions[token][m]:
                        login_sessions[token].pop(m)
            if request.args.get('embed') == '1':
                return render_token_section_partial(token, 'login')
            else:
                return redirect(url_for("status", token=token))
        elif "delete_all_logins" in request.form:
            login_sessions[token].clear()
            if request.args.get('embed') == '1':
                return render_token_section_partial(token, 'login')
            else:
                return redirect(url_for("status", token=token))

    # If embed -> return partial per section
    if request.args.get('embed') == '1':
        section = request.args.get('section', 'otp')
        return render_token_section_partial(token, section)

    # Full token dashboard page (for token user)
    html = f"""
    <html>
    <head>
        <title>{token} Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background:#f9f9f9; margin:0; }}
            h2 {{ margin:20px 0; text-align:center; color:#2C3E50; }}
            .container {{ display:flex; min-height:100vh; }}
            .sidebar {{ width:220px; background:#2C3E50; padding:20px; color:white; }}
            .sidebar button {{ margin-bottom:15px; width:100%; padding:10px; border:none; background:#3498DB; color:white; cursor:pointer; border-radius:6px; font-weight:700; }}
            .content {{ flex-grow:1; padding:30px; }}
            .card {{ background:white; padding:16px; border-radius:8px; box-shadow:0px 4px 14px rgba(0,0,0,0.06); }}
            table {{ border-collapse: collapse; width:100%; background:white; }}
            th, td {{ border:1px solid #ddd; padding:8px; }}
            th {{ background:#2980B9; color:white; }}
        </style>
        <script>
            function loadSection(section) {{
                var url = window.location.pathname + '?embed=1&section=' + section;
                document.getElementById('right_panel').innerHTML = '<div class="card"><p>Loading...</p></div>';
                fetch(url, {{ credentials: 'same-origin' }})
                    .then(function(r){{ return r.text(); }})
                    .then(function(html){{ document.getElementById('right_panel').innerHTML = html; }})
                    .catch(function(e){{ document.getElementById('right_panel').innerHTML = '<div class="card" style="color:red">Failed to load</div>'; }});
            }}
            window.onload = function() {{ loadSection('otp'); }}
        </script>
    </head>
    <body>
        <h2>KM OTP Dashboard ({token})</h2>
        <div class="container">
            <div class="sidebar">
                <button onclick="loadSection('otp')">OTP DATA</button>
                <button onclick="loadSection('login')">LOGIN DETECTIONS</button>
                <button onclick="loadSection('change_password')">CHANGE PASSWORD</button>
                <a href="/logout" style="color:white;text-decoration:none;"><button>LOGOUT</button></a>
            </div>
            <div class="content">
                <div id="right_panel" class="card">
                    <!-- dynamic content will load here -->
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# =========================
# Run App
# =========================
if __name__ == '__main__':
    app.run(debug=True)
