from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)

# In-memory storage for latest OTP
latest_otp_data = {
    "otp": None,
    "sim_number": None,
    "token": None,
    "timestamp": None
}

@app.route('/api/receive-otp', methods=['POST'])
def receive_otp():
    try:
        data = request.get_json(force=True)
        otp = data.get('otp')
        sim_number = data.get('sim_number')
        token = data.get('token')

        latest_otp_data['otp'] = otp
        latest_otp_data['sim_number'] = sim_number
        latest_otp_data['token'] = token
        latest_otp_data['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"[{latest_otp_data['timestamp']}] 📱 OTP from App - OTP: {otp}, SIM: {sim_number}, Token: {token}")
        return jsonify({"status": "success", "message": "OTP stored"}), 200

    except Exception as e:
        print("Error receiving from app:", e)
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    token = request.args.get("token")
    sim_number = request.args.get("sim_number")

    if not token or not sim_number:
        return jsonify({"status": "error", "message": "Missing token or sim_number"}), 400

    # Validate token & sim_number match the last received OTP
    if latest_otp_data["otp"] and latest_otp_data["token"] == token and latest_otp_data["sim_number"] == sim_number:
        response = {
            "status": "success",
            "otp": latest_otp_data["otp"],
            "sim_number": latest_otp_data["sim_number"],
            "timestamp": latest_otp_data["timestamp"]
        }

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🖥️ OTP sent to browser - OTP: {latest_otp_data['otp']}")

        # Clear OTP after sending to browser
        latest_otp_data["otp"] = None
        latest_otp_data["sim_number"] = None
        latest_otp_data["token"] = None
        latest_otp_data["timestamp"] = None

        return jsonify(response), 200
    else:
        return jsonify({"status": "empty", "message": "No OTP available or token mismatch"}), 404

@app.route('/api/status', methods=['GET'])
def status():
    """Check if the app has sent any OTP recently without clearing."""
    if latest_otp_data["timestamp"]:
        return jsonify({
            "status": "active",
            "last_otp": latest_otp_data["otp"],
            "sim_number": latest_otp_data["sim_number"],
            "last_received": latest_otp_data["timestamp"]
        }), 200
    else:
        return jsonify({"status": "inactive", "message": "No OTPs received yet"}), 200

# Only needed for local development
if __name__ == '__main__':
    app.run(debug=True)
