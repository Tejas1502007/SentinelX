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


@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    """
    Retrieve historical, persistent alert and clear events from SQLite.
    Query param: ?limit=50 (default 50, capped at 200)
    """
    limit = request.args.get("limit", default=50, type=int)
    alerts = reader.get_alerts(limit=limit)
    return jsonify({"count": len(alerts), "alerts": alerts})


@app.route("/api/alerts/<int:alert_id>/ack", methods=["POST"])
def acknowledge_alert(alert_id):
    """
    Acknowledge a specific alert event in SQLite.
    """
    success = reader.acknowledge_alert(alert_id)
    if success:
        return jsonify({"success": True, "alert_id": alert_id})
    return jsonify({"error": f"Failed to acknowledge alert {alert_id}"}), 400


@app.route("/api/ingest", methods=["POST"])
def ingest_telemetry():
    """
    Accept JSON telemetry packets from ESP32 over Wi-Fi (phone hotspot or LAN).
    Validates structure, types, and persists readings to SQLite.
    """
    if not request.is_json:
        return jsonify({
            "error": "Invalid Content-Type",
            "details": "Expected 'application/json'"
        }), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload:
        return jsonify({
            "error": "Invalid payload",
            "details": "Request body must be a non-empty JSON object"
        }), 400

    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)

    try:
        result = reader.ingest_telemetry(payload, client_ip=client_ip)
        return jsonify(result), 200
    except ValueError as ve:
        app.logger.warning(f"Telemetry validation error from {client_ip}: {ve}")
        return jsonify({
            "error": "Validation error",
            "details": str(ve)
        }), 400
    except Exception as e:
        app.logger.error(f"Telemetry database/ingest error from {client_ip}: {e}", exc_info=True)
        return jsonify({
            "error": "Database error",
            "details": str(e)
        }), 500


@app.route("/api/config", methods=["GET", "POST"])
def configure():
    """
    Get or dynamically update serial port, baudrate, or telemetry source mode ('auto', 'wifi', 'serial').
    Expected POST body: {"port": "COM5", "baudrate": 115200, "source": "wifi"}
    """
    if request.method == "GET":
        return jsonify({
            "port": reader.port,
            "baudrate": reader.baudrate,
            "source_mode": reader.source_mode,
            "active_source": reader.active_source
        })

    payload = request.get_json(silent=True) or {}
    new_source = payload.get("source")
    new_port = payload.get("port")
    new_baud = payload.get("baudrate", 115200)

    updated = {}

    if new_source:
        try:
            reader.set_source(new_source)
            updated["source_mode"] = new_source
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    if new_port:
        reader.set_port(port=new_port, baudrate=new_baud)
        updated["port"] = new_port
        updated["baudrate"] = new_baud

    if not updated:
        return jsonify({"error": "Missing parameters. Expected 'source', 'port', or 'baudrate'."}), 400

    return jsonify({
        "success": True,
        "message": f"Configuration updated: {updated}",
        **updated
    })


@app.route("/api/motion/config", methods=["GET", "POST"])
def motion_config():
    """
    Get or dynamically update experimental rule-based motion thresholds.
    GET: returns active thresholds and units.
    POST: accepts JSON with { inactivity_duration_s, impact_accel_peak_mps2, impact_cooldown_s, ... }
    """
    if request.method == "GET":
        return jsonify(reader.get_motion_config())

    payload = request.get_json(silent=True) or {}
    try:
        updated = reader.update_motion_config(payload)
        return jsonify({
            "success": True,
            "message": "Experimental motion thresholds updated",
            "config": updated
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/gas/config", methods=["GET", "POST"])
def gas_config():
    """
    Get or dynamically update MQ-135 gas warning & critical thresholds.
    GET: returns active thresholds and units.
    POST: accepts JSON with { warn_threshold_adc, clear_warn_adc, crit_threshold_adc, clear_crit_adc }
    """
    if request.method == "GET":
        return jsonify(reader.get_gas_config())

    payload = request.get_json(silent=True) or {}
    try:
        updated = reader.update_gas_config(payload)
        return jsonify({
            "success": True,
            "message": "Gas sensor thresholds updated",
            "config": updated
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/gas/baseline", methods=["POST", "DELETE"])
def gas_baseline():
    """Capture or reset the audited MQ-135 response baseline."""
    payload = request.get_json(silent=True) or {}
    operator_identity = payload.get("operator_identity")
    try:
        if request.method == "POST":
            baseline = reader.capture_gas_baseline(operator_identity)
            return jsonify({"success": True, "message": "Baseline captured", "baseline": baseline})
        baseline = reader.reset_gas_baseline(operator_identity)
        return jsonify({"success": True, "message": "Baseline reset", "baseline": baseline})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        app.logger.error(f"Gas baseline update failed: {e}", exc_info=True)
        return jsonify({"error": "Gas baseline update failed"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Sentinel-X Backend Server running on http://0.0.0.0:{port}")
    print(f"Wi-Fi Ingest endpoint available at: http://0.0.0.0:{port}/api/ingest")
    print(f"Telemetry source mode: {reader.source_mode}")
    print(f"Default Serial Port: {DEFAULT_PORT} @ {DEFAULT_BAUD} baud")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
