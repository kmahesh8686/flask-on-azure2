from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Store OTPs as a list
otp_storage = []

# Track browser sessions: {(token, sim_number): {"first_request": datetime}}
client_sessions = {}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@app.route('/api/receive-otp', methods=['POST'])
def receive_otp():
    try:
        data = request.get_json(force=True)
        otp = data.get('otp')
        sim_number = data.get('sim_number') or "UnknownSIM"
        token = data.get('token') or "unknown"

        entry = {
            "otp": otp,
            "sim_number": sim_number,
            "token": token,
            "timestamp": datetime.now()
        }
        otp_storage.append(entry)

        print(f"[{now_str()}] 📱 OTP received - OTP: {otp}, SIM: {sim_number}, Token: {token}")
        return jsonify({"status": "success", "message": "OTP stored"}), 200

    except Exception as e:
        print("Error receiving from app:", e)
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    token = request.args.get('token')
    sim_number = request.args.get('sim_number')

    if not token or not sim_number:
        return jsonify({"status": "error", "message": "token and sim_number required"}), 400

    key = (token, sim_number)

    # Initialize session if first time
    if key not in client_sessions:
        client_sessions[key] = {"first_request": datetime.now()}
        print(f"[{now_str()}] 🖥️ Browser started polling for {key}")

    session = client_sessions[key]
    first_request_time = session["first_request"]

    # Filter OTPs for this token/sim that arrived after first poll
    new_otps = [
        o for o in otp_storage
        if o["token"] == token
        and o["sim_number"] == sim_number
        and o["timestamp"] > first_request_time
    ]

    if new_otps:
        latest = new_otps[-1]

        # ✅ Clean up OTP storage for this token/sim
        otp_storage[:] = [o for o in otp_storage if not (
            o["token"] == token and o["sim_number"] == sim_number
        )]

        # ✅ Remove session after sending OTP
        client_sessions.pop(key, None)

        print(f"[{now_str()}] 🖥️ OTP sent to browser: {latest['otp']} for {key} and session cleaned")
        return jsonify({
            "status": "success",
            "otp": latest["otp"],
            "sim_number": latest["sim_number"],
            "timestamp": latest["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        }), 200
    else:
        return jsonify({"status": "empty", "message": "No new OTP available"}), 200


@app.route('/api/status', methods=['GET'])
def status():
    """Check if app is sending OTPs recently."""
    if not otp_storage:
        return jsonify({"status": "idle", "message": "No OTPs received yet"}), 200
    last_otp = otp_storage[-1]
    return jsonify({
        "status": "active",
        "last_otp": last_otp["otp"],
        "sim_number": last_otp["sim_number"],
        "timestamp": last_otp["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
    }), 200


if __name__ == '__main__':
    app.run(debug=True)
