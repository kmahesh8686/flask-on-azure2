from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime

app = Flask(__name__)
CORS(app)  # ✅ Enable CORS for all routes

# In-memory storage
latest_otp_data = {
    "otp": None,
    "vehicle": None,
    "timestamp": None
}

@app.route('/api/receive-otp', methods=['POST'])
def receive_otp():
    try:
        data = request.get_json(force=True)
        otp = data.get('otp')
        vehicle = data.get('vehicle')

        latest_otp_data['otp'] = otp
        latest_otp_data['vehicle'] = vehicle
        latest_otp_data['timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"[{latest_otp_data['timestamp']}] 📱 OTP from App - OTP: {otp}, Vehicle: {vehicle}")

        return jsonify({"status": "success", "message": "OTP stored"}), 200

    except Exception as e:
        print("Error receiving from app:", e)
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/get-latest-otp', methods=['GET'])
def get_latest_otp():
    if latest_otp_data["otp"]:
        return jsonify({
            "status": "success",
            "otp": latest_otp_data["otp"],
            "vehicle": latest_otp_data["vehicle"],
            "timestamp": latest_otp_data["timestamp"]
        }), 200
    else:
        return jsonify({
            "status": "empty",
            "message": "No OTP received yet"
        }), 404

# Only required for local run — Azure uses WSGI entry point (gunicorn/uwsgi)
if __name__ == '__main__':
    app.run(debug=True)
