from flask import Flask, request, jsonify
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

lock = threading.Lock()

# Keeps track of assignment counts per target name
assignments = {}

@app.route('/assign-vehicle', methods=['POST'])
def assign_vehicle():
    global assignments

    with lock:
        data = request.get_json()
        target_name = data.get("targetName")
        presets = data.get("presets")

        if not target_name or not presets:
            return jsonify({"error": "Missing 'targetName' or 'presets'"}), 400

        vehicle_list = presets.get(target_name)
        if not vehicle_list:
            return jsonify({"vehicle_number": None})  # No vehicles mapped to this target

        count = assignments.get(target_name, 0)

        if count < len(vehicle_list):
            assigned_vehicle = vehicle_list[count]
        else:
            assigned_vehicle = None  # No more vehicles left

        assignments[target_name] = count + 1

        return jsonify({"vehicle_number": assigned_vehicle})

# This part is only used when testing locally
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
