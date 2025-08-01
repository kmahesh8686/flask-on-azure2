from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Predefined tokens
valid_tokens = {"km8686", "km7676", "km0001", "km0002", "km0003"}

# Store OTPs here: key = token-sim, value = {"otp": ..., "vehicle": ...}
otp_storage = {}

@app.route("/api/receive-otp", methods=["POST"])
def receive_otp():
    data = request.get_json()
    token = data.get("token")
    sim = data.get("sim")
    otp = data.get("otp")
    vehicle = data.get("vehicle")  # Optional

    if not token or not sim or not otp:
        return jsonify({"status": "error", "message": "Missing data"}), 400
    if token not in valid_tokens:
        return jsonify({"status": "error", "message": "Invalid token"}), 403

    key = f"{token}-{sim}"
    otp_storage[key] = {"otp": otp, "vehicle": vehicle}
    return jsonify({"status": "success", "message": "OTP stored"}), 200

@app.route("/api/get-latest-otp", methods=["GET"])
def get_latest_otp():
    token = request.args.get("token")
    sim = request.args.get("sim")

    if not token or not sim:
        return jsonify({"status": "error", "message": "Missing token or sim"}), 400

    key = f"{token}-{sim}"
    otp_data = otp_storage.get(key)

    if otp_data:
        otp = otp_data["otp"]
        # Remove it after sending
        del otp_storage[key]
        return jsonify({"status": "success", "otp": otp}), 200
    else:
        return jsonify({"status": "error", "message": "OTP not found"}), 404

@app.route("/api/otp-exists", methods=["GET"])
def otp_exists():
    if otp_storage:
        return jsonify({
            "status": "success",
            "message": "OTPs available",
            "keys": list(otp_storage.keys())
        }), 200
    else:
        return jsonify({
            "status": "empty",
            "message": "No OTPs stored"
        }), 200

if __name__ == "__main__":
    app.run(debug=True)
