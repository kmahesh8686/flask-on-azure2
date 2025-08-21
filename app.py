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
