import os
import atexit
from flask import Flask, render_template, jsonify, request
from serial_reader import SerialReader

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Initialize SerialReader (defaults to COM5 at 115200 baud)
DEFAULT_PORT = os.environ.get("SENTINEL_PORT", "COM5")
DEFAULT_BAUD = int(os.environ.get("SENTINEL_BAUD", 115200))

reader = SerialReader(port=DEFAULT_PORT, baudrate=DEFAULT_BAUD)
reader.start()

# Ensure serial and db workers close cleanly when Flask stops
atexit.register(lambda: reader.stop())


@app.route("/")
def index():
    """Render the Sentinel-X dashboard."""
    return render_template("index.html")


@app.route("/api/ports", methods=["GET"])
def get_available_ports():
    """List all hardware serial ports detected on the host system."""
    ports = SerialReader.list_available_ports()
    return jsonify({"ports": ports})


@app.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    """
    Returns the latest sensor telemetry, connection state, gas reading, and emergency button.
    Never crashes even if hardware is disconnected.
    """
    telemetry = reader.get_telemetry()
    return jsonify(telemetry)


@app.route("/api/history", methods=["GET"])
def get_history():
    """
    Retrieve recent historical records from SQLite with safety limits.
    Query param: ?limit=60 (default 60, capped at 500)
    """
    limit = request.args.get("limit", default=60, type=int)
    history = reader.get_history(limit=limit)
    return jsonify({"count": len(history), "records": history})


@app.route("/api/config", methods=["POST"])
def configure_port():
    """
    Dynamically update the COM port or baudrate without restarting the server.
    Expected JSON body: {"port": "COM5", "baudrate": 115200}
    """
    payload = request.get_json(silent=True) or {}
    new_port = payload.get("port")
    new_baud = payload.get("baudrate", 115200)

    if not new_port:
        return jsonify({"error": "Missing 'port' parameter in request"}), 400

    reader.set_port(port=new_port, baudrate=new_baud)
    return jsonify({
        "success": True,
        "message": f"Serial port updated to {new_port} @ {new_baud} baud",
        "port": new_port,
        "baudrate": new_baud
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Sentinel-X Backend Server running on http://127.0.0.1:{port}")
    print(f"Default Serial Port: {DEFAULT_PORT} @ {DEFAULT_BAUD} baud")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
