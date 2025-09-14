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
# Storage per token
# =========================
mobile_otps = {t: [] for t in PREDEFINED_TOKENS}
vehicle_otps = {t: [] for t in PREDEFINED_TOKENS}
otp_data = {t: [] for t in PREDEFINED_TOKENS}      # all delivered/removed OTPs with reasons
client_sessions = {t: {} for t in PREDEFINED_TOKENS}
browser_queues = {t: {} for t in PREDEFINED_TOKENS}
login_sessions = {t: {} for t in PREDEFINED_TOKENS}

BROWSER_STALE_SECONDS = float(10)

def valid_token(token: str) -> bool:
    return token in PREDEFINED_TOKENS

# =========================
# Helpers
# =========================
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
            try: queues[identifier].remove(b)
            except ValueError: pass
            sessions.pop((identifier, b), None)
            continue
        last = sess.get("last_request", 0)
        first_req_dt = sess.get("first_request", datetime.now(IST))
        if now_ts - last > BROWSER_STALE_SECONDS:
            try: queues[identifier].remove(b)
            except ValueError: pass
            sessions.pop((identifier, b), None)
            # Move pending mobile OTPs that arrived after first_request
            for p in list(mobile_otps[token]):
                if (p.get("sim_number") or "").upper() == identifier.upper() and p.get("timestamp") and p["timestamp"] > first_req_dt:
                    try: mobile_otps[token].remove(p)
                    except ValueError: pass
                    mark_otp_removed_to_data(token, p, reason="stale_browser", browser_id=b)
            # Move vehicle OTPs
            for p in list(vehicle_otps[token]):
                if (p.get("vehicle") or "").upper() == identifier.upper() and p.get("timestamp") and p["timestamp"] > first_req_dt:
                    try: vehicle_otps[token].remove(p)
                    except ValueError: pass
                    mark_otp_removed_to_data(token, p, reason="stale_browser", browser_id=b)

# =========================
# OTP APIs
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
            try: vehicle_otps[token].remove(latest)
            except ValueError: pass
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
        exceeded = [o for o in otp_data[token] if o.get("sim_number") == sim_number and o.get("removed_reason") == "limit_exceeded"]
        if exceeded:
            return jsonify({"status": "error", "message": "limit_exceeded"}), 403

        new_otps = [o for o in mobile_otps[token] if o.get("sim_number","").upper() == sim_number and o["timestamp"] > session_time]
        if new_otps and next_browser == browser_id:
            latest = new_otps[0]
            try: mobile_otps[token].remove(latest)
            except ValueError: pass
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

# =========================
# Login detection APIs
# =========================
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
# Admin Login + Dashboard
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
        return redirect(url_for("admin_dashboard"))
    return render_template_string(admin_login_page, error=None)

@app.route('/admin-logout')
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

@app.route('/admin', methods=['GET','POST'])
def admin_dashboard():
    global ADMIN_PASSWORD
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))

    msg = ""
    if request.method == 'POST':
        if "change_admin_password" in request.form:
            cur = request.form.get("current_password")
            new = request.form.get("new_password")
            confirm = request.form.get("confirm_password")
            if cur != ADMIN_PASSWORD:
                msg = "Current password incorrect"
            elif new != confirm:
                msg = "Passwords do not match"
            else:
                ADMIN_PASSWORD = new
                msg = "Password changed successfully"
        elif "set_cap" in request.form:
            t = request.form.get("token")
            cap = request.form.get("cap")
            if t in PREDEFINED_TOKENS:
                token_mobile_caps[t] = int(cap) if cap else None
                msg = f"Cap updated for {t}"

    caps_html = "".join(
        f"<tr><td>{t}</td>"
        f"<td>{len(token_processed_mobiles[t])}</td>"
        f"<td>{token_mobile_caps[t] if token_mobile_caps[t] else 'Unlimited'}</td>"
        f"<td><form method='POST' style='display:inline-block'>"
        f"<input type='hidden' name='token' value='{t}'>"
        f"<input type='number' name='cap' placeholder='Enter cap' style='padding:6px;width:110px;margin-right:6px;'>"
        f"<button type='submit' name='set_cap' style='padding:6px 10px;background:#2980B9;color:white;border:none;border-radius:4px;'>Set</button>"
        f"</form></td></tr>"
        for t in PREDEFINED_TOKENS
    )

    token_list_html = "".join(
        f"<li style='margin:8px 0;'><a href='/status/{t}' target='_blank' style='color:#2980B9;text-decoration:none;font-weight:700;'>{t}</a></li>"
        for t in PREDEFINED_TOKENS
    )

    limit_token_list_html = "".join(
        f"<li style='margin:8px 0;'><a href='/admin-limit/{t}' target='_blank' style='color:#2980B9;text-decoration:none;font-weight:700;'>{t}</a></li>"
        for t in PREDEFINED_TOKENS
    )

    html = f"""
    <html>
    <head>
        <title>Admin Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; margin:0; background:#f4f6f9; }}
            .container {{ display:flex; min-height:100vh; }}
            .sidebar {{
                width:260px; background:#2C3E50; color:white; display:flex; flex-direction:column;
                padding:22px; box-sizing:border-box;
            }}
            .sidebar h2 {{ margin:0 0 14px; font-size:20px; text-align:center; letter-spacing:1px; }}
            .sidebar button {{
                margin-bottom:12px; padding:12px; width:100%;
                border:none; background:#3498DB; color:white; cursor:pointer; border-radius:8px;
                font-size:14px; font-weight:600;
            }}
            .sidebar a.buttonlink {{ text-decoration:none; }}
            .content {{ flex-grow:1; padding:28px; }}
            h3 {{ margin-top:0; color:#2C3E50; }}
            table {{ border-collapse: collapse; width:100%; background:white; box-shadow:0px 4px 18px rgba(0,0,0,0.06); border-radius:8px; overflow:hidden; }}
            th, td {{ border-bottom:1px solid #eee; padding:12px 10px; text-align:center; }}
            th {{ background:#2980B9; color:white; font-weight:700; }}
            .card {{ background:white; padding:16px; border-radius:8px; box-shadow:0px 2px 6px rgba(0,0,0,0.04); }}
            ul.token-list {{ list-style:none; padding:0; margin:10px 0; }}
            .message {{ text-align:center; margin-bottom:14px; color:green; font-weight:600; }}
            label {{ display:block; margin-top:8px; color:#333; font-weight:600; font-size:13px; }}
            input[type=password], input[type=number] {{ padding:8px; margin-top:6px; width:100%; box-sizing:border-box; border-radius:6px; border:1px solid #ddd; }}
        </style>
        <script>
            function showSection(id) {{
                ['tokens_section','tokencap_section','limit_section','changepass_section'].forEach(function(s) {{
                    var el = document.getElementById(s);
                    if(el) el.style.display = 'none';
                }});
                if(id) document.getElementById(id).style.display = 'block';
            }}
            window.onload = function() {{ showSection('tokens_section'); }};
        </script>
    </head>
    <body>
        <div class="container">
            <div class="sidebar">
                <h2>ADMIN</h2>
                <button onclick="showSection('tokens_section')">TOKENS</button>
                <button onclick="showSection('tokencap_section')">TOKEN CAP</button>
                <button onclick="showSection('limit_section')">LIMIT EXCEEDED</button>
                <button onclick="showSection('changepass_section')">CHANGE PASSWORD</button>
                <a href="/admin-logout" class="buttonlink"><button style="background:#E74C3C;margin-top:14px;">LOGOUT</button></a>
            </div>
            <div class="content">
                <div class="message">{msg}</div>
                <div id="tokens_section" class="card" style="display:none;">
                    <h3>Available Tokens</h3>
                    <ul class="token-list">{token_list_html}</ul>
                    <p style="color:#666">Click a token to open its dashboard in a new tab.</p>
                </div>

                <div id="tokencap_section" class="card" style="display:none;">
                    <h3>Token Mobile Caps</h3>
                    <table>
                        <tr><th>Token</th><th>Processed Mobiles</th><th>Cap</th><th>Set Cap</th></tr>
                        {caps_html}
                    </table>
                </div>

                <div id="limit_section" class="card" style="display:none;">
                    <h3>Limit Exceeded</h3>
                    <ul class="token-list">{limit_token_list_html}</ul>
                    <p style="color:#666">Click a token to open the limit-exceeded list in a new tab.</p>
                </div>

                <div id="changepass_section" class="card" style="display:none;">
                    <h3>Change Admin Password</h3>
                    <form method="POST">
                        <label>Current Password</label>
                        <input type="password" name="current_password" required>
                        <label>New Password</label>
                        <input type="password" name="new_password" required>
                        <label>Confirm Password</label>
                        <input type="password" name="confirm_password" required>
                        <div style="margin-top:12px;">
                            <button type="submit" name="change_admin_password" style="background:#27AE60;padding:10px 14px;border-radius:6px;border:none;color:white;font-weight:700;">Change Password</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# =========================
# Admin - limit-exceeded per token (with delete)
# =========================
@app.route('/admin-limit/<token>', methods=['GET','POST'])
def admin_limit(token):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if token not in PREDEFINED_TOKENS:
        return "Invalid token", 404

    if request.method == 'POST':
        if "delete_selected" in request.form:
            to_delete = [int(x) for x in request.form.getlist("otp_rows")]
            otp_data[token] = [
                e for i, e in enumerate(otp_data[token])
                if not (i in to_delete and e.get("removed_reason") == "limit_exceeded")
            ]
        elif "delete_all" in request.form:
            otp_data[token] = [e for e in otp_data[token] if e.get("removed_reason") != "limit_exceeded"]
        return redirect(url_for("admin_limit", token=token))

    rows = ""
    for i, e in enumerate(otp_data[token]):
        if e.get("removed_reason") == "limit_exceeded":
            ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
            rows += f"""
            <tr>
                <td><input type='checkbox' name='otp_rows' value='{i}'></td>
                <td>{e.get('sim_number','')}</td>
                <td>{e.get('vehicle','')}</td>
                <td>{e.get('otp','')}</td>
                <td>{e.get('browser_id','')}</td>
                <td>{ts}</td>
                <td>{e.get('removed_reason','')}</td>
            </tr>
            """

    html = f"""
    <html>
    <head>
        <title>Limit Exceeded - {token}</title>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#f9f9f9; margin:0; padding:20px; }}
            h2 {{ text-align:center; color:#2C3E50; }}
            form {{ max-width:1100px; margin:20px auto; }}
            table {{ border-collapse: collapse; width:100%; background:white; box-shadow:0px 6px 20px rgba(0,0,0,0.06); border-radius:8px; overflow:hidden; }}
            th, td {{ padding:12px 10px; text-align:center; border-bottom:1px solid #eee; }}
            th {{ background:#E74C3C; color:white; font-weight:700; }}
            button {{ margin:10px 6px; padding:10px 16px; background:#E74C3C; color:white; border:none; border-radius:6px; cursor:pointer; font-weight:700; }}
            button[name="delete_all"] {{ background:#C0392B; }}
        </style>
    </head>
    <body>
        <h2>Limit Exceeded OTPs - {token}</h2>
        <form method="POST">
            <table>
                <tr>
                    <th>Select</th><th>MOBILE</th><th>VEHICLE</th><th>OTP</th><th>BROWSER ID</th><th>DATE</th><th>Reason</th>
                </tr>
                {rows if rows else '<tr><td colspan="7">No limit-exceeded OTPs found</td></tr>'}
            </table>
            <div style="text-align:center; margin-top:12px;">
                <button type="submit" name="delete_selected">Delete Selected</button>
                <button type="submit" name="delete_all">Delete All</button>
            </div>
        </form>
    </body>
    </html>
    """
    return html

# =========================
# Token login/dashboard
# =========================
login_page_html = """
<html><head><title>Login</title></head>
<body style="font-family:Segoe UI, Arial, sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;background:#f4f6f9;">
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
    # ✅ Allow admin direct access OR token-login access
    if not (("token" in session and session["token"] == token) or session.get("is_admin")):
        return redirect(url_for("login"))

    # OTP table (all otp_data entries for this token)
    otp_rows = ""
    for i, e in enumerate(otp_data[token]):
        ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
        otp_rows += f"""
        <tr>
            <td><input type='checkbox' name='otp_rows' value='{i}'></td>
            <td>{e.get('sim_number','')}</td>
            <td>{e.get('vehicle','')}</td>
            <td>{e.get('otp','')}</td>
            <td>{e.get('browser_id','')}</td>
            <td>{ts}</td>
            <td>{e.get('removed_reason','')}</td>
        </tr>
        """

    # Login table
    login_rows = ""
    for m, entries in login_sessions[token].items():
        for i, e in enumerate(entries):
            login_rows += f"""
            <tr>
                <td><input type='checkbox' name='login_rows' value='{m}:{i}'></td>
                <td>{m}</td>
                <td>{e['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td>
                <td>{e.get('source','')}</td>
            </tr>
            """

    if request.method == 'POST':
        if "delete_selected_otps" in request.form:
            to_delete = [int(x) for x in request.form.getlist("otp_rows")]
            otp_data[token] = [e for i, e in enumerate(otp_data[token]) if i not in to_delete]
            return redirect(url_for("status", token=token))
        elif "delete_all_otps" in request.form:
            otp_data[token].clear()
            return redirect(url_for("status", token=token))
        elif "delete_selected_logins" in request.form:
            to_delete = request.form.getlist("login_rows")
            for x in to_delete:
                m, idx = x.split(":")
                idx = int(idx)
                if m in login_sessions[token] and 0 <= idx < len(login_sessions[token][m]):
                    login_sessions[token][m].pop(idx)
                    if not login_sessions[token][m]:
                        login_sessions[token].pop(m)
            return redirect(url_for("status", token=token))
        elif "delete_all_logins" in request.form:
            login_sessions[token].clear()
            return redirect(url_for("status", token=token))

    html = f"""
    <html>
    <head>
        <title>{token} Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background:#f9f9f9; margin:0; }}
            h2 {{ margin:20px 0; text-align:center; color:#2C3E50; }}
            .container {{ display:flex; min-height:100vh; }}
            .sidebar {{ width:220px; background:#2C3E50; padding:20px; color:white; }}
            .sidebar button {{ margin-bottom:15px; width:100%; padding:10px; border:none; background:#3498DB; color:white; cursor:pointer; border-radius:6px; font-weight:700; }}
            .content {{ flex-grow:1; padding:30px; }}
            table {{ border-collapse: collapse; width:100%; background:white; box-shadow:0px 4px 14px rgba(0,0,0,0.06); border-radius:8px; overflow:hidden; }}
            th, td {{ border-bottom:1px solid #eee; padding:10px; text-align:center; }}
            th {{ background:#2980B9; color:white; font-weight:700; }}
            label {{ display:block; margin-top:6px; font-weight:700; color:#333; }}
            input[type=password] {{ padding:8px; margin-top:6px; width:100%; box-sizing:border-box; border-radius:6px; border:1px solid #ddd; }}
            button.action {{ padding:8px 12px; border-radius:6px; border:none; cursor:pointer; font-weight:700; }}
        </style>
        <script>
            function showSection(id){{
                document.getElementById('otp_section').style.display = id=='otp_section'?'block':'none';
                document.getElementById('login_section').style.display = id=='login_section'?'block':'none';
                document.getElementById('change_password').style.display = id=='change_password'?'block':'none';
            }}
            window.onload = function(){{ showSection('otp_section'); }};
        </script>
    </head>
    <body>
        <h2>KM OTP Dashboard ({token})</h2>
        <div class="container">
            <div class="sidebar">
                <button onclick="showSection('otp_section')">OTP DATA</button>
                <button onclick="showSection('login_section')">LOGIN DETECTIONS</button>
                <button onclick="showSection('change_password')">CHANGE PASSWORD</button>
                <a href="/logout" style="color:white;text-decoration:none;"><button style="width:100%;padding:10px;border:none;border-radius:6px;background:#E74C3C;font-weight:700;">LOGOUT</button></a>
            </div>
            <div class="content">
                <div id="otp_section" style="display:none;">
                    <div style="max-width:1100px;margin:0 auto;">
                    <h3>OTP Data</h3>
                    <form method="POST">
                        <table>
                            <tr><th>Select</th><th>MOBILE</th><th>VEHICLE</th><th>OTP</th><th>BROWSER ID</th><th>DATE</th><th>Reason</th></tr>
                            {otp_rows if otp_rows else '<tr><td colspan="7">No OTPs found</td></tr>'}
                        </table>
                        <div style="margin-top:12px;">
                            <button class="action" type="submit" name="delete_selected_otps" style="background:#E74C3C;color:white;">Delete Selected</button>
                            <button class="action" type="submit" name="delete_all_otps" style="background:#C0392B;color:white;">Delete All</button>
                        </div>
                    </form>
                    </div>
                </div>
                <div id="login_section" style="display:none;">
                    <div style="max-width:1100px;margin:0 auto;">
                    <h3>Login Detections</h3>
                    <form method="POST">
                        <table>
                            <tr><th>Select</th><th>MOBILE</th><th>DATE</th><th>SOURCE</th></tr>
                            {login_rows if login_rows else '<tr><td colspan="4">No login detections</td></tr>'}
                        </table>
                        <div style="margin-top:12px;">
                            <button class="action" type="submit" name="delete_selected_logins" style="background:#E74C3C;color:white;">Delete Selected</button>
                            <button class="action" type="submit" name="delete_all_logins" style="background:#C0392B;color:white;">Delete All</button>
                        </div>
                    </form>
                    </div>
                </div>
                <div id="change_password" style="display:none;">
                    <div style="max-width:600px;margin:0 auto;">
                        <h3>Change Password</h3>
                        <form method="POST" action="/change-password/{token}">
                            <label>Current Password</label>
                            <input type="password" name="current_password" required>
                            <label>New Password</label>
                            <input type="password" name="new_password" required>
                            <label>Confirm Password</label>
                            <input type="password" name="confirm_password" required>
                            <div style="margin-top:12px;">
                                <button class="action" type="submit" style="background:#27AE60;color:white;">Change Password</button>
                            </div>
                        </form>
                        {"<p style='color:red;'>Current password incorrect.</p>" if request.args.get('err')=='wrong_current' else ""}
                        {"<p style='color:red;'>New passwords do not match.</p>" if request.args.get('err')=='nomatch' else ""}
                        {"<p style='color:green;'>Password changed successfully.</p>" if request.args.get('msg')=='changed' else ""}
                    </div>
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
