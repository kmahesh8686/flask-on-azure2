from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # ✅ Enable CORS for all origins

# 🔐 Valid token
VALID_TOKEN = "abc12"

# 🧠 Temporary in-memory storage
mobile_otp_dict = {}     # sim_number -> otp
vehicle_otp_dict = {}    # vehicle_number -> otp


@app.route('/otp', methods=['POST'])
def receive_otp():
    token = request.form.get('token', '').strip()
    sim = request.form.get('sim', '').strip()
    otp = request.form.get('otp', '').strip()
    vehicle = request.form.get('vehicle', '').strip()

    if token != VALID_TOKEN:
        return jsonify({"status": "unauthorized", "message": "Invalid token"}), 403

    if not otp:
        return jsonify({"status": "error", "message": "OTP missing"}), 400

    if vehicle:
        vehicle_otp_dict[vehicle] = otp
        print(f"[VEHICLE] Stored -> {vehicle}: {otp}")
    elif sim:
        mobile_otp_dict[sim] = otp
        print(f"[MOBILE] Stored -> {sim}: {otp}")
    else:
        return jsonify({"status": "error", "message": "Missing SIM and vehicle"}), 400

    return jsonify({"status": "success", "message": "OTP stored"}), 200


@app.route('/get/mobile/<mobile>', methods=['GET'])
def get_mobile_otp(mobile):
    otp = mobile_otp_dict.get(mobile)
    if otp:
        return jsonify({"status": "success", "otp": otp}), 200
    return jsonify({"status": "not_found", "message": "No OTP found"}), 404


@app.route('/get/vehicle/<vehicle>', methods=['GET'])
def get_vehicle_otp(vehicle):
    otp = vehicle_otp_dict.get(vehicle)
    if otp:
        return jsonify({"status": "success", "otp": otp}), 200
    return jsonify({"status": "not_found", "message": "No OTP found"}), 404


@app.route('/list/mobile', methods=['GET'])
def list_mobile():
    return jsonify(mobile_otp_dict)


@app.route('/list/vehicle', methods=['GET'])
def list_vehicle():
    return jsonify(vehicle_otp_dict)
