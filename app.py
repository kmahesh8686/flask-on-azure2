from flask import Flask, request, jsonify, redirect, url_for
from flask_cors import CORS
from datetime import datetime
import zoneinfo
import threading

app = Flask(__name__)
CORS(app)

# =========================
# In-memory storage
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

def purge_unsent_otps_for_token_and_identifier(token: str, identifier: str, cutoff_time: datetime):
    """
    Remove any stored mobile/vehicle OTPs for the given (token + identifier)
    whose timestamp <= cutoff_time.
    """
    t = normalize_token(token)
    idu = normalize_identifier(identifier)
    if not t or not idu:
        return

    with store_lock:
        # Purge vehicle OTPs matching token+vehicle with timestamp <= cutoff_time
        before_v = len(vehicle_otps)
        vehicle_otps[:] = [
            o for o in vehicle_otps
            if not (
                (o.get("token", "").strip() == t)
                and (o.get("vehicle", "").upper() == idu)
                and (o.get("timestamp") is not None)
                and (o["timestamp"] <= cutoff_time)
            )
        ]
        removed_v = before_v - len(vehicle_otps)

        # Purge mobile OTPs matching token+sim_number with timestamp <= cutoff_time
        before_m = len(mobile_otps)
        mobile_otps[:] = [
            o for o in mobile_otps
            if not (
                (o.get("token", "").strip() == t)
                and (o.get("sim_number", "").upper() == idu)
                and (o.get("timestamp") is not None)
                and (o["timestamp"] <= cutoff_time)
            )
        ]
        removed_m = before_m - len(mobile_otps)

    app.logger.debug(f"purge_unsent: token={t} identifier={idu} cutoff={cutoff_time} removed_v={removed_v} removed_m={removed_m}")

def add_browser_to_queue(token: str, identifier: str, browser_id: str):
    """
    Register/refresh the browser in the queue for (token, identifier).
    - Remove any existing occurrence of browser_id in that queue (so it is refreshed),
      then place it at the FRONT of the queue (so it's the next to receive OTP).
    - Set client_sessions[(token, identifier, browser_id)] = {"first_request": now()}
    - Immediately purge any stored OTPs for the same token+identifier with timestamp <= first_request.
    """
    t = normalize_token(token)
    idu = normalize_identifier(identifier)
    bid = (browser_id or "").strip()
    if not t or not idu or not bid:
        return

    cutoff = now()
    key = (t, idu)

    with store_lock:
        q = browser_queues.get(key)
        if q is None:
            browser_queues[key] = [bid]
        else:
            # Remove old occurrences of this browser_id and re-insert at front
            newq = [b for b in q if b != bid]
            browser_queues[key] = [bid] + newq

        # Set/overwrite first_request for this browser-session tuple
        client_sessions[(t, idu, bid)] = {"first_request": cutoff}
        app.logger.debug(f"add_browser_to_queue: token={t} id={idu} browser_id={bid} first_request={cutoff} queue={browser_queues.get(key)}")

    # Immediately purge any previously queued/old OTPs for this token+identifier
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

    # Add/refresh browser (sets first_request to now and purges older unsent OTPs)
    add_browser_to_queue(token, identifier, browser_id)

    session_key = (normalize_token(token), normalize_identifier(identifier), browser_id)
    with store_lock:
        session_time = client_sessions.get(session_key, {}).get("first_request", now())
    next_browser = get_next_browser(token, identifier)

    app.logger.debug(f"get_latest_otp called: token={token} identifier={identifier} browser_id={browser_id} session_time={session_time} next_browser={next_browser}")

    # Vehicle OTPs path
    if vehicle:
        with store_lock:
            new_otps = [
                o for o in vehicle_otps
                if o.get("token", "").strip() == token
                and o.get("vehicle", "").upper() == vehicle
                and o.get("timestamp") is not None
                and o["timestamp"] > session_time
            ]
        app.logger.debug(f"vehicle new_otps found={len(new_otps)} for token={token} vehicle={vehicle}")
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
                and o.get("timestamp") is not None
                and o["timestamp"] > session_time
            ]
        app.logger.debug(f"mobile new_otps found={len(new_otps)} for token={token} sim={sim_number}")
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
        <style>body {{ font-family: 'Segoe UI', sans-serif; background:#f9f9f9; margin:0; }}</style>
    </head>
    <body>
        <h2>KM OTP Dashboard</h2>
        <div>{otp_rows if otp_rows else '<p>No OTPs found</p>'}</div>
    </body>
    </html>
    """
    return html

# =========================
# Login detection endpoints
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
                {"timestamp": e["timestamp"].strftime("%Y-%m-%d %H:%M:%S"), "source": e.get('source',"")}
                for e in login_sessions[mobile_number]
            ]
            return jsonify({"status": "found", "mobile_number": mobile_number, "detections": detections}), 200
    return jsonify({"status": "not_found", "mobile_number": mobile_number}), 200

# =========================
# Run
# =========================
if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=5000)
