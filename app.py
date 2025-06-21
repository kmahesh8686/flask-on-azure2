from flask import Flask, request, jsonify
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)

lock = threading.Lock()
assignments = {}
presets_store = {}

@app.route('/set-presets', methods=['POST'])
def set_presets():
    global presets_store
    data = request.get_json()
    new_presets = data.get("presets")
    if not new_presets:
        return jsonify({"error": "Missing 'presets'"}), 400

    with lock:
        presets_store = new_presets
    return jsonify({"message": "Presets received", "count": len(presets_store)})

@app.route('/assign-vehicle', methods=['POST'])
def assign_vehicle():
    global assignments

    data = request.get_json()
    target_name = data.get("targetName")

    if not target_name:
        return jsonify({"error": "Missing 'targetName'"}), 400

    with lock:
        vehicle_list = presets_store.get(target_name)
        if not vehicle_list:
            return jsonify({"vehicle_number": None})

        count = assignments.get(target_name, 0)

        if count < len(vehicle_list):
            assigned_vehicle = vehicle_list[count]
        else:
            assigned_vehicle = None

        assignments[target_name] = count + 1
        return jsonify({"vehicle_number": assigned_vehicle})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
