from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

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

        print(f"[{latest_otp_data['timestamp']}] 📱 OTP received - OTP: {otp}, SIM: {sim_number}, Token: {token}")
        return jsonify({"status": "success", "message": "OTP stored"}), 200

    except Exception as e:
        print("Error receiving from app:", e)
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    # Browser must send token and sim_number as query params
    req_token = request.args.get("token")
    req_sim_number = request.args.get("sim_number")

    if not req_token or not req_sim_number:
        return jsonify({"status": "error", "message": "Missing token or sim_number"}), 400

    # Check if OTP exists and token + sim_number match
    if latest_otp_data["otp"] and \
       latest_otp_data["token"] == req_token and \
       latest_otp_data["sim_number"] == req_sim_number:

        response = {
            "status": "success",
            "otp": latest_otp_data["otp"],
            "sim_number": latest_otp_data["sim_number"],
            "token": latest_otp_data["token"],
            "timestamp": latest_otp_data["timestamp"]
        }

        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🖥️ OTP sent to browser - OTP: {latest_otp_data['otp']}")

        # Clear OTP after serving
        latest_otp_data["otp"] = None
        latest_otp_data["sim_number"] = None
        latest_otp_data["token"] = None
        latest_otp_data["timestamp"] = None

        return jsonify(response), 200

    elif latest_otp_data["otp"]:
        # OTP exists but token/sim mismatch
        return jsonify({"status": "forbidden", "message": "Invalid token or sim_number"}), 403
    else:
        return jsonify({"status": "empty", "message": "No OTP available"}), 404


if __name__ == '__main__':
    app.run(debug=True)
