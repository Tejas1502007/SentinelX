import json
import logging
import math
import queue
import sqlite3
import threading
import time
import os
from collections import deque
import serial
import serial.tools.list_ports
from motion_analyzer import MotionAnalyzer

# Configure module logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SentinelX_Serial")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sentinel_x.db")


class SerialReader:
    """
    Non-blocking, thread-safe serial communication & SQLite persistence manager.
    Reads newline-delimited JSON packets from ESP32, auto-reconnects on dropouts,
    and logs records asynchronously into SQLite so serial reads are never blocked.
    """

    def __init__(self, port="COM5", baudrate=115200, db_path=DB_PATH):
        self.port = port
        self.baudrate = baudrate
        self.db_path = db_path
        self.serial_conn = None
        self.is_running = False
        self.serial_thread = None
        self.db_thread = None
        self.db_queue = queue.Queue(maxsize=1000)
        self.lock = threading.RLock()  # Reentrant: update_gas_config calls get_gas_config internally

        # Telemetry source arbitration: "auto", "wifi", or "serial"
        self.source_mode = os.environ.get("SENTINEL_SOURCE", "serial").lower()
        if self.source_mode not in ("auto", "wifi", "serial"):
            self.source_mode = "serial"
        self.active_source = "none"
        self.last_wifi_time = 0.0

        # Alert threshold definitions and state
        self.TEMP_ALERT_HIGH_C = 31.0  # Alert triggers strictly > 31.0°C
        self.TEMP_ALERT_LOW_C = 30.0   # Alert clears <= 30.0°C
        self.temp_alert_active = False
        self.temp_alert_status = "NORMAL"
        self.sos_active = False

        # Gas alert threshold definitions (Provisional demonstration thresholds based on clean air baseline ~50 ADC)
        self.GAS_ALERT_WARN_ADC = 400   # Warning triggers strictly >= 400 ADC (~0.32 V)
        self.GAS_CLEAR_WARN_ADC = 350   # Warning clears strictly < 350 ADC (~0.28 V) (50 ADC hysteresis)
        self.GAS_ALERT_CRIT_ADC = 1000  # Critical triggers strictly >= 1000 ADC (~0.81 V)
        self.GAS_CLEAR_CRIT_ADC = 850   # Critical clears strictly < 850 ADC (~0.68 V) (150 ADC hysteresis)
        self.gas_alert_active = False
        self.gas_alert_level = "NORMAL" # "NORMAL", "WARNING", "CRITICAL"
        self.gas_alert_status = "NORMAL"

        self.GAS_RESPONSE_WINDOW_SECONDS = 10.0
        self.GAS_RESPONSE_TREND_DEADBAND_ADC = 5
        self.GAS_BASELINE_MAX_SPREAD_ADC = 15
        self.GAS_BASELINE_MIN_SAMPLES = 5
        self.GAS_BASELINE_MIN_DURATION_SECONDS = 4.0
        self.gas_response_samples = deque(maxlen=600)
        self.gas_baseline = None
        self._last_mq_sample_age_ms = None

        # Experimental rule-based motion analysis engine
        self.motion_analyzer = MotionAnalyzer()

        # Thread-safe telemetry state
        self.data = {
            "connected": False,
            "status": "Initializing",
            "port": self.port,
            "baudrate": self.baudrate,
            "source_mode": self.source_mode,
            "telemetry_source": "none",
            "client_ip": None,
            "last_update": None,
            "uptime_ms": None,
            "dht_status": "disconnected",
            "mpu_status": "disconnected",
            "mq_status": "disconnected",
            "temperature": None,
            "humidity": None,
            "temperature_alert": {
                "status": "NORMAL",
                "active": False,
                "high_threshold": self.TEMP_ALERT_HIGH_C,
                "low_threshold": self.TEMP_ALERT_LOW_C,
                "current_temperature": None,
                "sensor_status": "disconnected"
            },
            "accel": {"x": 0.0, "y": 0.0, "z": 0.0},
            "gyro": {"x": 0.0, "y": 0.0, "z": 0.0},
            "gas_adc": None,
            "gas_voltage_est": None,
            "gas_response": {
                "baseline_adc": None,
                "baseline_captured_at": None,
                "baseline_operator_identity": None,
                "relative_change_percent": None,
                "trend": "STABLE",
                "peak_adc_60s": None,
                "status": "SENSOR FAULT",
                "response_level": "NORMAL",
                "baseline_capture_ready": False,
                "baseline_capture_reason": "Baseline not configured; capture requires stable, valid readings after warm-up.",
                "trend_window_seconds": self.GAS_RESPONSE_WINDOW_SECONDS,
                "trend_deadband_adc": self.GAS_RESPONSE_TREND_DEADBAND_ADC,
                "baseline_max_spread_adc": self.GAS_BASELINE_MAX_SPREAD_ADC,
                "baseline_stability_sample_count": 0,
                "baseline_stability_span_adc": None,
            },
            "gas_alert": {
                "status": "NORMAL",
                "active": False,
                "level": "NORMAL",
                "warn_threshold_adc": self.GAS_ALERT_WARN_ADC,
                "warn_threshold_voltage": round((self.GAS_ALERT_WARN_ADC / 4095.0) * 3.3, 3),
                "crit_threshold_adc": self.GAS_ALERT_CRIT_ADC,
                "crit_threshold_voltage": round((self.GAS_ALERT_CRIT_ADC / 4095.0) * 3.3, 3),
                "clear_warn_adc": self.GAS_CLEAR_WARN_ADC,
                "clear_crit_adc": self.GAS_CLEAR_CRIT_ADC,
                "current_adc": None,
                "current_voltage": None,
                "sensor_status": "disconnected",
                "sound_active": False
            },
            "emergency_button": "RELEASED",
            "buzzer": "STANDBY",
            "motion": None,
            "motion_analysis": None,
            "diagnostics": None,
            "raw_log": []
        }

        self._init_database()

    def _init_database(self):
        """Initialize SQLite schema for telemetry history and persistent alert events."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sensor_telemetry (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        uptime_ms INTEGER,
                        dht_status TEXT,
                        mpu_status TEXT,
                        temperature REAL,
                        humidity REAL,
                        accel_x REAL,
                        accel_y REAL,
                        accel_z REAL,
                        gyro_x REAL,
                        gyro_y REAL,
                        gyro_z REAL,
                        gas_adc INTEGER,
                        gas_voltage REAL,
                        emergency_button TEXT
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_telemetry_time ON sensor_telemetry(timestamp DESC)")

                # Persistent alert events table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS alert_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        alert_type TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        message TEXT NOT NULL,
                        sensor_value REAL,
                        threshold_value REAL,
                        acknowledged INTEGER DEFAULT 0
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_time ON alert_events(timestamp DESC)")

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS gas_baseline_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        baseline_adc INTEGER,
                        timestamp REAL NOT NULL,
                        operator_identity TEXT NOT NULL
                    )
                """)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_gas_baseline_events_time ON gas_baseline_events(timestamp DESC)")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS gas_response_config (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        trend_window_seconds REAL NOT NULL,
                        trend_deadband_adc INTEGER NOT NULL,
                        baseline_max_spread_adc INTEGER NOT NULL
                    )
                """)
                cursor.execute("""
                    INSERT OR IGNORE INTO gas_response_config (
                        id, trend_window_seconds, trend_deadband_adc, baseline_max_spread_adc
                    ) VALUES (1, ?, ?, ?)
                """, (
                    self.GAS_RESPONSE_WINDOW_SECONDS,
                    self.GAS_RESPONSE_TREND_DEADBAND_ADC,
                    self.GAS_BASELINE_MAX_SPREAD_ADC,
                ))
                cursor.execute("""
                    SELECT trend_window_seconds, trend_deadband_adc, baseline_max_spread_adc
                    FROM gas_response_config WHERE id = 1
                """)
                gas_config = cursor.fetchone()
                if gas_config:
                    self.GAS_RESPONSE_WINDOW_SECONDS = float(gas_config[0])
                    self.GAS_RESPONSE_TREND_DEADBAND_ADC = int(gas_config[1])
                    self.GAS_BASELINE_MAX_SPREAD_ADC = int(gas_config[2])

                cursor.execute("""
                    SELECT event_type, baseline_adc, timestamp, operator_identity
                    FROM gas_baseline_events ORDER BY id DESC LIMIT 1
                """)
                last_baseline_event = cursor.fetchone()
                if last_baseline_event and last_baseline_event[0] == "CAPTURE":
                    self.gas_baseline = {
                        "baseline_adc": last_baseline_event[1],
                        "captured_at": last_baseline_event[2],
                        "operator_identity": last_baseline_event[3],
                    }
                    self.data["gas_response"].update({
                        "baseline_adc": last_baseline_event[1],
                        "baseline_captured_at": last_baseline_event[2],
                        "baseline_operator_identity": last_baseline_event[3],
                        "baseline_capture_reason": "Baseline configured; capture requires controlled, repeatable ambient conditions.",
                    })

                # Restore previous temperature alert state across restarts if available
                cursor.execute("""
                    SELECT event_type, sensor_value FROM alert_events
                    WHERE alert_type = 'TEMPERATURE_ALERT'
                    ORDER BY timestamp DESC LIMIT 1
                """)
                last_temp_event = cursor.fetchone()
                if last_temp_event and last_temp_event[0] == "TRIGGERED":
                    self.temp_alert_active = True
                    self.temp_alert_status = "ALERT"
                    self.data["temperature_alert"]["active"] = True
                    self.data["temperature_alert"]["status"] = "ALERT"
                    self.data["temperature_alert"]["current_temperature"] = last_temp_event[1]

                conn.commit()
            logger.info(f"SQLite database and alert_events initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize SQLite database: {e}")

    def _record_alert_event(self, alert_type, event_type, severity, message, sensor_value=None, threshold_value=None):
        """Persist a single deduplicated alert or clear event into SQLite."""
        now = time.time()
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO alert_events (
                        timestamp, alert_type, event_type, severity, message, sensor_value, threshold_value, acknowledged
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """, (now, alert_type, event_type, severity, message, sensor_value, threshold_value))
                conn.commit()
            logger.info(f"Alert Event Recorded: [{event_type}] {message}")
        except Exception as e:
            logger.error(f"Failed to record alert event to SQLite: {e}")

    def get_alerts(self, limit=50):
        """Retrieve historical alert events from SQLite."""
        limit = min(max(1, int(limit)), 200)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM alert_events
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    item = dict(r)
                    item["time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["timestamp"]))
                    item["acknowledged"] = bool(item.get("acknowledged", 0))
                    results.append(item)
                return results
        except Exception as e:
            logger.error(f"Failed to fetch alert events from SQLite: {e}")
            return []

    def acknowledge_alert(self, alert_id):
        """Acknowledge a specific alert event in SQLite."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE alert_events SET acknowledged = 1 WHERE id = ?", (alert_id,))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to acknowledge alert {alert_id}: {e}")
            return False

    @staticmethod
    def list_available_ports():
        """Returns a list of dictionaries describing all connected serial ports."""
        ports = serial.tools.list_ports.comports()
        result = []
        for p in ports:
            result.append({
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid
            })
        return result

    def set_port(self, port, baudrate=None):
        """Dynamically update port configuration and reconnect."""
        with self.lock:
            self.port = port
            if baudrate:
                self.baudrate = int(baudrate)
            self.data["port"] = self.port
            self.data["baudrate"] = self.baudrate

        self._disconnect_hardware()

    def set_source(self, mode):
        """
        Dynamically update telemetry source mode: 'auto', 'wifi', or 'serial'.
        """
        mode = str(mode).strip().lower()
        if mode not in ("auto", "wifi", "serial"):
            raise ValueError(f"Invalid telemetry source mode: '{mode}'. Must be 'auto', 'wifi', or 'serial'.")

        with self.lock:
            self.source_mode = mode
            self.data["source_mode"] = mode
            if mode == "wifi":
                self.active_source = "wifi"
                if not self.data["connected"]:
                    self.data["status"] = "Waiting for Wi-Fi Telemetry"
            elif mode == "serial":
                self.active_source = "serial"
                if not self.data["connected"]:
                    self.data["status"] = f"Connecting to {self.port}..."
            elif mode == "auto":
                if not self.data["connected"]:
                    self.data["status"] = "Waiting for Telemetry (Auto)"
            self._append_log(f"Source mode updated to '{mode}'.")

        if mode == "wifi":
            self._disconnect_hardware()

        logger.info(f"Telemetry source mode set to '{mode}'.")

    def start(self):
        """Start both serial worker and async database worker."""
        if self.is_running:
            return
        self.is_running = True

        # 1. Start Serial Reader Thread
        self.serial_thread = threading.Thread(target=self._run_serial_loop, daemon=True, name="SerialWorker")
        self.serial_thread.start()

        # 2. Start Async DB Writer Thread
        self.db_thread = threading.Thread(target=self._run_db_writer, daemon=True, name="DatabaseWorker")
        self.db_thread.start()

        logger.info(f"Serial and Database workers started for {self.port} @ {self.baudrate}.")

    def stop(self):
        """Gracefully stop background threads and release resources."""
        self.is_running = False
        self._disconnect_hardware()
        if self.serial_thread and self.serial_thread.is_alive():
            self.serial_thread.join(timeout=1.0)
        with self.lock:
            self.data["connected"] = False
            self.data["status"] = "Stopped"
        logger.info("Serial and Database workers stopped.")

    def _disconnect_hardware(self):
        """Safely close active serial connection."""
        if self.serial_conn:
            try:
                if self.serial_conn.is_open:
                    self.serial_conn.close()
            except Exception as e:
                logger.warning(f"Error while closing serial port: {e}")
            finally:
                self.serial_conn = None

        with self.lock:
            self.data["connected"] = False
            self.data["status"] = "Disconnected"

    def _append_log(self, message):
        """Keep a ring-buffer of the latest 30 log lines."""
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self.data["raw_log"].append(entry)
        if len(self.data["raw_log"]) > 30:
            self.data["raw_log"].pop(0)

    def _run_serial_loop(self):
        """Continuous background serial polling with auto-reconnection and source arbitration."""
        while self.is_running:
            # If explicit Wi-Fi mode is set, keep serial disconnected
            if self.source_mode == "wifi":
                if self.serial_conn:
                    self._disconnect_hardware()
                time.sleep(1.0)
                continue

            # In auto mode, if Wi-Fi has streamed recently (< 5.0s), serial backs off
            now = time.time()
            if self.source_mode == "auto" and (now - self.last_wifi_time < 5.0):
                time.sleep(0.5)
                continue

            if not self.serial_conn or not self.serial_conn.is_open:
                try:
                    with self.lock:
                        if self.active_source != "wifi" or (now - self.last_wifi_time >= 5.0):
                            self.data["status"] = f"Connecting to {self.port}..."
                    self.serial_conn = serial.Serial(
                        port=self.port,
                        baudrate=self.baudrate,
                        timeout=1.5
                    )
                    self.serial_conn.reset_input_buffer()
                    with self.lock:
                        self.active_source = "serial"
                        self.data["connected"] = True
                        self.data["telemetry_source"] = "serial"
                        self.data["status"] = "Connected (Serial)"
                        self._append_log(f"Connected to {self.port} @ {self.baudrate} baud.")
                    logger.info(f"Successfully opened serial port {self.port}.")
                except (serial.SerialException, FileNotFoundError, PermissionError) as e:
                    with self.lock:
                        # Only set disconnected status if Wi-Fi is not actively streaming
                        if self.active_source != "wifi" or (time.time() - self.last_wifi_time >= 5.0):
                            self.data["connected"] = False
                            self.data["status"] = f"Cannot open {self.port}: {type(e).__name__}"
                            self._append_log(f"Connection error on {self.port}: {e}")
                    self.serial_conn = None
                    time.sleep(2.0)
                    continue

            try:
                raw_bytes = self.serial_conn.readline()
                if not raw_bytes:
                    continue

                line = raw_bytes.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                # In auto mode, if Wi-Fi packet arrived while reading, drop serial to prioritize Wi-Fi
                if self.source_mode == "auto" and (time.time() - self.last_wifi_time < 5.0):
                    continue

                self._parse_line(line)

            except (serial.SerialException, OSError) as e:
                logger.warning(f"Hardware disconnected on {self.port}: {e}")
                with self.lock:
                    if self.active_source != "wifi" or (time.time() - self.last_wifi_time >= 5.0):
                        self.data["connected"] = False
                        self.data["status"] = "Hardware Disconnected"
                        self._append_log(f"Device disconnected: {e}")
                self._disconnect_hardware()
                time.sleep(2.0)
            except Exception as e:
                logger.error(f"Unexpected read loop error: {e}")
                time.sleep(0.5)

    def _parse_line(self, line):
        """Parse incoming JSON packet from serial and enqueue for SQLite persistence."""
        with self.lock:
            self._append_log(line)

        if not (line.startswith("{") and line.endswith("}")):
            return

        try:
            packet = json.loads(line)
        except json.JSONDecodeError:
            with self.lock:
                self._append_log(f"Malformed JSON ignored: {line}")
            return

        self._process_telemetry_packet(packet, source="serial")

    def _process_telemetry_packet(self, packet, source="serial", client_ip=None):
        """
        Common processor for validated packets from either Serial or Wi-Fi.
        Updates thread-safe memory state and enqueues to SQLite.
        """
        now = time.time()

        accel = packet.get("accel")
        gyro = packet.get("gyro")

        with self.lock:
            self.data["last_update"] = now
            self.data["connected"] = True
            self.data["telemetry_source"] = source
            self.data["uptime_ms"] = packet.get("uptime_ms")
            self.data["dht_status"] = packet.get("dht_status", "unknown")
            self.data["mpu_status"] = packet.get("mpu_status", "unknown")

            self.data["temperature"] = packet.get("temperature")
            self.data["humidity"] = packet.get("humidity")

            # --- TEMPERATURE ALERT THRESHOLD EVALUATION WITH HYSTERESIS ---
            temp = self.data["temperature"]
            dht_status = self.data["dht_status"]
            temp_valid = (
                temp is not None
                and isinstance(temp, (int, float))
                and not isinstance(temp, bool)
                and dht_status == "ok"
            )

            if temp_valid:
                temp_val = float(temp)
                # 1. Trigger threshold: strictly > 31.0°C
                if temp_val > self.TEMP_ALERT_HIGH_C:
                    if not self.temp_alert_active:
                        self.temp_alert_active = True
                        self.temp_alert_status = "ALERT"
                        self._record_alert_event(
                            alert_type="TEMPERATURE_ALERT",
                            event_type="TRIGGERED",
                            severity="Warning",
                            message=f"High temperature threshold exceeded: {temp_val:.1f}°C (Threshold: >{self.TEMP_ALERT_HIGH_C:.1f}°C)",
                            sensor_value=temp_val,
                            threshold_value=self.TEMP_ALERT_HIGH_C
                        )
                    else:
                        self.temp_alert_status = "ALERT"
                # 2. Clear threshold: <= 30.0°C
                elif temp_val <= self.TEMP_ALERT_LOW_C:
                    if self.temp_alert_active:
                        self.temp_alert_active = False
                        self.temp_alert_status = "NORMAL"
                        self._record_alert_event(
                            alert_type="TEMPERATURE_ALERT",
                            event_type="CLEARED",
                            severity="Info",
                            message=f"Temperature normalized: {temp_val:.1f}°C (Clear Threshold: <={self.TEMP_ALERT_LOW_C:.1f}°C)",
                            sensor_value=temp_val,
                            threshold_value=self.TEMP_ALERT_LOW_C
                        )
                    else:
                        self.temp_alert_status = "NORMAL"
                # 3. Hysteresis band (30.0 < temp <= 31.0): Maintain previous alert state without toggling
                else:
                    self.temp_alert_status = "ALERT" if self.temp_alert_active else "NORMAL"
            else:
                # Missing, invalid, or stale DHT reading: Never treat as safe or clear an alert!
                if self.temp_alert_active:
                    self.temp_alert_status = "ALERT"
                else:
                    self.temp_alert_status = "UNAVAILABLE"

            incoming_sound = False
            if isinstance(packet.get("temperature_alert"), dict):
                incoming_sound = bool(packet["temperature_alert"].get("sound_active", False))

            sensor_status = dht_status if (dht_status and dht_status != "ok") else ("ok" if temp_valid else "unavailable")
            self.data["temperature_alert"] = {
                "status": self.temp_alert_status,
                "active": self.temp_alert_active,
                "high_threshold": self.TEMP_ALERT_HIGH_C,
                "low_threshold": self.TEMP_ALERT_LOW_C,
                "current_temperature": temp if temp_valid else None,
                "sensor_status": sensor_status,
                "sound_active": incoming_sound
            }

            if isinstance(accel, dict):
                self.data["accel"] = {
                    "x": accel.get("x", 0.0),
                    "y": accel.get("y", 0.0),
                    "z": accel.get("z", 0.0)
                }
            else:
                self.data["accel"] = None

            if isinstance(gyro, dict):
                self.data["gyro"] = {
                    "x": gyro.get("x", 0.0),
                    "y": gyro.get("y", 0.0),
                    "z": gyro.get("z", 0.0)
                }
            else:
                self.data["gyro"] = None

            # --- MQ-135 GAS SENSOR & ALERT EVALUATION WITH HYSTERESIS ---
            gas_adc = packet.get("gas_adc")
            gas_voltage = packet.get("gas_voltage_est")
            mq_status = packet.get("mq_status")
            if not mq_status and isinstance(packet.get("diagnostics"), dict):
                mq_status = packet["diagnostics"].get("mq_status", "unknown")
            if not mq_status:
                mq_status = "unknown"

            self.data["mq_status"] = mq_status
            self.data["gas_adc"] = gas_adc
            self.data["gas_voltage_est"] = gas_voltage

            gas_valid = (
                gas_adc is not None
                and isinstance(gas_adc, (int, float))
                and not isinstance(gas_adc, bool)
                and math.isfinite(float(gas_adc))
                and 0 <= float(gas_adc) <= 4095
                and mq_status in ("ok", "unknown")
            )

            if gas_valid:
                adc_val = int(gas_adc)
                volt_val = round((adc_val / 4095.0) * 3.3, 3) if gas_voltage is None else round(float(gas_voltage), 3)

                # 1. Critical Gas Trigger: >= GAS_ALERT_CRIT_ADC (1000)
                if adc_val >= self.GAS_ALERT_CRIT_ADC:
                    if self.gas_alert_level != "CRITICAL":
                        event_action = "ESCALATED" if self.gas_alert_level == "WARNING" else "TRIGGERED"
                        self.gas_alert_active = True
                        self.gas_alert_level = "CRITICAL"
                        self.gas_alert_status = "CRITICAL"
                        self._record_alert_event(
                            alert_type="GAS_ALERT",
                            event_type=event_action,
                            severity="Emergency",
                            message=f"CRITICAL MQ-135 response threshold reached: ADC {adc_val} ({volt_val} V >= {self.GAS_ALERT_CRIT_ADC}).",
                            sensor_value=adc_val,
                            threshold_value=self.GAS_ALERT_CRIT_ADC
                        )
                    else:
                        self.gas_alert_status = "CRITICAL"

                # 2. In Critical state: check clear/de-escalation below GAS_CLEAR_CRIT_ADC (850)
                elif self.gas_alert_level == "CRITICAL":
                    if adc_val < self.GAS_CLEAR_CRIT_ADC:
                        if adc_val >= self.GAS_ALERT_WARN_ADC:
                            self.gas_alert_level = "WARNING"
                            self.gas_alert_status = "WARNING"
                            self._record_alert_event(
                                alert_type="GAS_ALERT",
                                event_type="DE-ESCALATED",
                                severity="Warning",
                                message=f"MQ-135 response de-escalated to WARNING: ADC {adc_val} ({volt_val} V < {self.GAS_CLEAR_CRIT_ADC} clear threshold)",
                                sensor_value=adc_val,
                                threshold_value=self.GAS_CLEAR_CRIT_ADC
                            )
                        elif adc_val < self.GAS_CLEAR_WARN_ADC:
                            self.gas_alert_active = False
                            self.gas_alert_level = "NORMAL"
                            self.gas_alert_status = "NORMAL"
                            self._record_alert_event(
                                alert_type="GAS_ALERT",
                                event_type="CLEARED",
                                severity="Info",
                                message=f"MQ-135 response returned below warning threshold: ADC {adc_val} ({volt_val} V < {self.GAS_CLEAR_WARN_ADC} clear threshold)",
                                sensor_value=adc_val,
                                threshold_value=self.GAS_CLEAR_WARN_ADC
                            )
                    else:
                        # In critical hysteresis band (850 <= adc < 1000): maintain CRITICAL
                        self.gas_alert_status = "CRITICAL"

                # 3. Warning Trigger: >= GAS_ALERT_WARN_ADC (400)
                elif adc_val >= self.GAS_ALERT_WARN_ADC:
                    if self.gas_alert_level != "WARNING":
                        self.gas_alert_active = True
                        self.gas_alert_level = "WARNING"
                        self.gas_alert_status = "WARNING"
                        self._record_alert_event(
                            alert_type="GAS_ALERT",
                            event_type="TRIGGERED",
                            severity="Warning",
                            message=f"Elevated MQ-135 response threshold reached: ADC {adc_val} ({volt_val} V >= {self.GAS_ALERT_WARN_ADC}).",
                            sensor_value=adc_val,
                            threshold_value=self.GAS_ALERT_WARN_ADC
                        )
                    else:
                        self.gas_alert_status = "WARNING"

                # 4. In Warning state: check clear below GAS_CLEAR_WARN_ADC (350)
                elif self.gas_alert_level == "WARNING":
                    if adc_val < self.GAS_CLEAR_WARN_ADC:
                        self.gas_alert_active = False
                        self.gas_alert_level = "NORMAL"
                        self.gas_alert_status = "NORMAL"
                        self._record_alert_event(
                            alert_type="GAS_ALERT",
                            event_type="CLEARED",
                            severity="Info",
                            message=f"MQ-135 response returned below warning threshold: ADC {adc_val} ({volt_val} V < {self.GAS_CLEAR_WARN_ADC} clear threshold)",
                            sensor_value=adc_val,
                            threshold_value=self.GAS_CLEAR_WARN_ADC
                        )
                    else:
                        # In warning hysteresis band (350 <= adc < 400): maintain WARNING
                        self.gas_alert_status = "WARNING"

                # 5. Normal clean air
                else:
                    self.gas_alert_active = False
                    self.gas_alert_level = "NORMAL"
                    self.gas_alert_status = "NORMAL"

            else:
                # Missing, invalid, or stale MQ-135 reading: Never treat as safe or clear an alert!
                if self.gas_alert_active:
                    self.gas_alert_status = self.gas_alert_level
                else:
                    self.gas_alert_status = "SENSOR_FAULT" if mq_status in ("error", "disconnected") else ("WARMING_UP" if mq_status == "warming_up" else "UNKNOWN")

            incoming_gas_sound = False
            if isinstance(packet.get("gas_alert"), dict):
                incoming_gas_sound = bool(packet["gas_alert"].get("sound_active", False))

            gas_sensor_status = mq_status if (mq_status and mq_status != "ok") else ("ok" if gas_valid else "unavailable")
            self.data["gas_alert"] = {
                "status": self.gas_alert_status,
                "active": self.gas_alert_active,
                "level": self.gas_alert_level,
                "warn_threshold_adc": self.GAS_ALERT_WARN_ADC,
                "warn_threshold_voltage": round((self.GAS_ALERT_WARN_ADC / 4095.0) * 3.3, 3),
                "crit_threshold_adc": self.GAS_ALERT_CRIT_ADC,
                "crit_threshold_voltage": round((self.GAS_ALERT_CRIT_ADC / 4095.0) * 3.3, 3),
                "clear_warn_adc": self.GAS_CLEAR_WARN_ADC,
                "clear_crit_adc": self.GAS_CLEAR_CRIT_ADC,
                "current_adc": int(gas_adc) if gas_valid else None,
                "current_voltage": round(float(gas_voltage), 3) if (gas_voltage is not None and gas_valid) else (round((int(gas_adc) / 4095.0) * 3.3, 3) if gas_valid else None),
                "sensor_status": gas_sensor_status,
                "sound_active": incoming_gas_sound
            }
            self._update_gas_response(packet, now, gas_adc, mq_status)

            self.data["emergency_button"] = packet.get("emergency_button", "RELEASED")

            # Priority Arbitration for buzzer output:
            # 1. SOS (highest - continuous tone)
            # 2. Critical Gas (rapid urgent pulse)
            # 3. Gas Warning (intermittent cadence)
            # 4. Temperature Alert (2 Hz pulse with timeout)
            # 5. Standby / Off
            if self.data["emergency_button"] == "PRESSED":
                self.data["buzzer"] = "ACTIVE"
                self.data["buzzer_mode"] = "SOS"
            elif self.gas_alert_active and self.gas_alert_level == "CRITICAL" and (incoming_gas_sound or True):
                self.data["buzzer"] = "ACTIVE"
                self.data["buzzer_mode"] = "GAS_CRITICAL"
            elif self.gas_alert_active and self.gas_alert_level == "WARNING" and (incoming_gas_sound or True):
                self.data["buzzer"] = "ACTIVE"
                self.data["buzzer_mode"] = "GAS_WARNING"
            elif self.temp_alert_active and packet.get("temperature_alert", {}).get("sound_active", False):
                self.data["buzzer"] = "ACTIVE"
                self.data["buzzer_mode"] = "TEMP_ALERT"
            else:
                self.data["buzzer"] = packet.get("buzzer", "STANDBY")
                self.data["buzzer_mode"] = packet.get("buzzer_mode", "OFF")

            self.data["motion"] = packet.get("motion")
            if not self.data["motion"] and isinstance(self.data["accel"], dict) and self.data["accel"].get("x") is not None:
                try:
                    ax = float(self.data["accel"].get("x") or 0.0)
                    ay = float(self.data["accel"].get("y") or 0.0)
                    az = float(self.data["accel"].get("z") or 0.0)
                    mag = (ax * ax + ay * ay + az * az) ** 0.5
                    gx = float(self.data["gyro"].get("x") or 0.0) if isinstance(self.data["gyro"], dict) else 0.0
                    gy = float(self.data["gyro"].get("y") or 0.0) if isinstance(self.data["gyro"], dict) else 0.0
                    gz = float(self.data["gyro"].get("z") or 0.0) if isinstance(self.data["gyro"], dict) else 0.0
                    gmag = (gx * gx + gy * gy + gz * gz) ** 0.5
                    self.data["motion"] = {
                        "accel_magnitude_mps2": round(mag, 3),
                        "accel_magnitude_g": round(mag / 9.80665, 3),
                        "gyro_magnitude_rads": round(gmag, 4),
                        "window_samples": 50,
                        "window_duration_ms": 1000,
                        "accel_mag_mean_mps2": round(mag, 3),
                        "accel_mag_peak_mps2": round(mag, 3),
                        "accel_mag_std_mps2": 0.02,
                        "gyro_mag_mean_rads": round(gmag, 4),
                        "gyro_mag_peak_rads": round(gmag, 4),
                        "window_complete": True
                    }
                except Exception:
                    pass

            self.data["diagnostics"] = packet.get("diagnostics")

            # --- EXPERIMENTAL RULE-BASED MOTION ANALYSIS ---
            try:
                motion_analysis, motion_evt = self.motion_analyzer.process(
                    current_time=now,
                    mpu_status=self.data["mpu_status"],
                    accel=self.data["accel"],
                    gyro=self.data["gyro"],
                    motion_features=self.data["motion"],
                    diagnostics=self.data["diagnostics"]
                )
                self.data["motion_analysis"] = motion_analysis
                if motion_evt:
                    self._record_alert_event(
                        alert_type=motion_evt["alert_type"],
                        event_type=motion_evt["event_type"],
                        severity=motion_evt["severity"],
                        message=motion_evt["message"],
                        sensor_value=motion_evt.get("sensor_value"),
                        threshold_value=motion_evt.get("threshold_value")
                    )
            except Exception as e:
                logger.error(f"Error evaluating motion analysis: {e}")

            btn = self.data["emergency_button"]
            if btn == "PRESSED" and not self.sos_active:
                self.sos_active = True
                self._record_alert_event(
                    alert_type="SOS_BUTTON",
                    event_type="TRIGGERED",
                    severity="Emergency",
                    message="Emergency SOS button pressed",
                    sensor_value=None,
                    threshold_value=None
                )
            elif btn == "RELEASED" and self.sos_active:
                self.sos_active = False
                self._record_alert_event(
                    alert_type="SOS_BUTTON",
                    event_type="CLEARED",
                    severity="Info",
                    message="Emergency SOS button released",
                    sensor_value=None,
                    threshold_value=None
                )

            if source == "wifi":
                self.active_source = "wifi"
                self.last_wifi_time = now
                self.data["client_ip"] = client_ip
                self.data["status"] = "Wi-Fi Telemetry Active"
                self._append_log(f"[Wi-Fi] Packet ingested (uptime: {packet.get('uptime_ms')}ms, SOS: {self.data['emergency_button']})")
            elif source == "serial":
                self.active_source = "serial"
                self.data["status"] = "Connected (Serial)"

        # Asynchronously enqueue record for SQLite batch insertion
        try:
            record = (
                now,
                packet.get("uptime_ms"),
                packet.get("dht_status"),
                packet.get("mpu_status"),
                packet.get("temperature"),
                packet.get("humidity"),
                accel.get("x") if isinstance(accel, dict) else None,
                accel.get("y") if isinstance(accel, dict) else None,
                accel.get("z") if isinstance(accel, dict) else None,
                gyro.get("x") if isinstance(gyro, dict) else None,
                gyro.get("y") if isinstance(gyro, dict) else None,
                gyro.get("z") if isinstance(gyro, dict) else None,
                packet.get("gas_adc"),
                packet.get("gas_voltage_est"),
                packet.get("emergency_button")
            )
            self.db_queue.put_nowait(record)
        except queue.Full:
            logger.warning("Database queue full, dropping oldest telemetry record.")

    def _valid_gas_samples_in_window(self, now, window_seconds):
        cutoff = now - window_seconds
        return [
            (timestamp, adc)
            for timestamp, adc in self.gas_response_samples
            if cutoff <= timestamp <= now
        ]

    def _baseline_capture_state(self, now, sensor_status=None):
        current_status = sensor_status or self.data["gas_response"]["status"]
        if current_status != "VALID":
            return False, "Baseline capture requires a VALID sensor after warm-up.", [], None
        if self.data["last_update"] is None or now - self.data["last_update"] > 5.0:
            return False, "Baseline capture requires fresh sensor readings.", [], None

        samples = self._valid_gas_samples_in_window(
            now, max(self.GAS_RESPONSE_WINDOW_SECONDS, self.GAS_BASELINE_MIN_DURATION_SECONDS)
        )
        if len(samples) < self.GAS_BASELINE_MIN_SAMPLES:
            return False, f"Need at least {self.GAS_BASELINE_MIN_SAMPLES} recent valid readings.", samples, None

        span_seconds = samples[-1][0] - samples[0][0]
        if span_seconds < self.GAS_BASELINE_MIN_DURATION_SECONDS:
            return False, "Valid readings must remain stable for at least 4 seconds.", samples, None

        adc_values = [adc for _, adc in samples]
        spread = max(adc_values) - min(adc_values)
        if spread > self.GAS_BASELINE_MAX_SPREAD_ADC:
            return False, "Recent readings are not stable enough to capture a baseline.", samples, spread

        return True, "Stable valid readings; baseline capture is ready.", samples, spread

    def _update_gas_response(self, packet, now, gas_adc, mq_status, record_sample=True):
        diagnostics = packet.get("diagnostics")
        sample_age_ms = diagnostics.get("mq_sample_age_ms") if isinstance(diagnostics, dict) else None
        if isinstance(sample_age_ms, (int, float)) and not isinstance(sample_age_ms, bool):
            self._last_mq_sample_age_ms = float(sample_age_ms)
        else:
            self._last_mq_sample_age_ms = None

        valid_adc = (
            isinstance(gas_adc, (int, float))
            and not isinstance(gas_adc, bool)
            and math.isfinite(float(gas_adc))
            and 0 <= float(gas_adc) <= 4095
        )
        adc_value = int(gas_adc) if valid_adc else None
        if mq_status == "warming_up":
            status = "WARMING UP"
        elif mq_status == "stale" or (
            self._last_mq_sample_age_ms is not None and self._last_mq_sample_age_ms > 3000
        ):
            status = "STALE"
        elif mq_status != "ok" or adc_value is None:
            status = "SENSOR FAULT"
        else:
            status = "VALID"

        if status == "VALID" and record_sample:
            self.gas_response_samples.append((now, adc_value))

        retention_seconds = max(60.0, self.GAS_RESPONSE_WINDOW_SECONDS)
        while self.gas_response_samples and now - self.gas_response_samples[0][0] > retention_seconds:
            self.gas_response_samples.popleft()

        recent = self._valid_gas_samples_in_window(now, self.GAS_RESPONSE_WINDOW_SECONDS)
        trend = "STABLE"
        if len(recent) >= 2:
            midpoint = max(1, len(recent) // 2)
            early_average = sum(value for _, value in recent[:midpoint]) / len(recent[:midpoint])
            late_average = sum(value for _, value in recent[midpoint:]) / len(recent[midpoint:])
            change = late_average - early_average
            if change > self.GAS_RESPONSE_TREND_DEADBAND_ADC:
                trend = "RISING"
            elif change < -self.GAS_RESPONSE_TREND_DEADBAND_ADC:
                trend = "FALLING"

        peak_samples = self._valid_gas_samples_in_window(now, 60.0)
        baseline = self.gas_baseline
        baseline_adc = baseline["baseline_adc"] if baseline else None
        relative_change = None
        if baseline_adc is not None and baseline_adc > 0 and adc_value is not None and status == "VALID":
            relative_change = round(((adc_value - baseline_adc) / baseline_adc) * 100.0, 1)

        capture_ready, capture_reason, stable_samples, spread = self._baseline_capture_state(now, status)
        self.data["gas_response"] = {
            "sensor_voltage_v": round((adc_value / 4095.0) * 3.3, 3) if adc_value is not None else None,
            "baseline_adc": baseline_adc,
            "baseline_captured_at": baseline["captured_at"] if baseline else None,
            "baseline_operator_identity": baseline["operator_identity"] if baseline else None,
            "relative_change_percent": relative_change,
            "trend": trend,
            "peak_adc_60s": max((value for _, value in peak_samples), default=None),
            "status": status,
            "response_level": self.gas_alert_level,
            "baseline_capture_ready": capture_ready,
            "baseline_capture_reason": capture_reason,
            "trend_window_seconds": self.GAS_RESPONSE_WINDOW_SECONDS,
            "trend_deadband_adc": self.GAS_RESPONSE_TREND_DEADBAND_ADC,
            "baseline_max_spread_adc": self.GAS_BASELINE_MAX_SPREAD_ADC,
            "baseline_stability_sample_count": len(stable_samples),
            "baseline_stability_span_adc": spread,
        }

    def capture_gas_baseline(self, operator_identity=None):
        """Capture an audited baseline from stable, post-warm-up ADC samples."""
        with self.lock:
            now = time.time()
            ready, reason, samples, _ = self._baseline_capture_state(now)
            if not ready:
                raise ValueError(reason)

            adc_value = int(round(sum(value for _, value in samples) / len(samples)))
            operator = str(operator_identity or "unspecified").strip()[:120] or "unspecified"
            captured_at = now
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO gas_baseline_events (event_type, baseline_adc, timestamp, operator_identity)
                    VALUES ('CAPTURE', ?, ?, ?)
                """, (adc_value, captured_at, operator))
                conn.commit()

            self.gas_baseline = {
                "baseline_adc": adc_value,
                "captured_at": captured_at,
                "operator_identity": operator,
            }
            self._update_gas_response(
                {"diagnostics": {"mq_sample_age_ms": self._last_mq_sample_age_ms}},
                now,
                self.data.get("gas_adc"),
                self.data.get("mq_status"),
                record_sample=False,
            )
            return dict(self.gas_baseline)

    def reset_gas_baseline(self, operator_identity=None):
        """Clear the active baseline while retaining an audit record."""
        with self.lock:
            operator = str(operator_identity or "unspecified").strip()[:120] or "unspecified"
            now = time.time()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO gas_baseline_events (event_type, baseline_adc, timestamp, operator_identity)
                    VALUES ('RESET', NULL, ?, ?)
                """, (now, operator))
                conn.commit()
            self.gas_baseline = None
            self._update_gas_response(
                {"diagnostics": {"mq_sample_age_ms": self._last_mq_sample_age_ms}},
                now,
                self.data.get("gas_adc"),
                self.data.get("mq_status"),
                record_sample=False,
            )
            return {"baseline_adc": None, "captured_at": None, "operator_identity": operator}

    def get_gas_baseline_events(self, limit=50):
        limit = min(max(1, int(limit)), 200)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT event_type, baseline_adc, timestamp, operator_identity
                FROM gas_baseline_events ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(row) for row in rows]

    def ingest_telemetry(self, packet, client_ip=None):
        """
        Validate and ingest telemetry received via HTTP POST from ESP32 over Wi-Fi.
        Raises ValueError on validation failure.
        """
        if self.source_mode == "serial":
            raise ValueError("Telemetry source mode is locked to 'serial'. Wi-Fi ingestion rejected.")

        if not isinstance(packet, dict) or not packet:
            raise ValueError("Telemetry payload must be a non-empty JSON object.")

        # Validate numeric and structured fields
        def validate_numeric(val, name, allow_none=True):
            if val is None:
                if not allow_none:
                    raise ValueError(f"Field '{name}' cannot be null.")
                return
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise ValueError(f"Field '{name}' must be numeric (received {type(val).__name__}: {val}).")

        validate_numeric(packet.get("uptime_ms"), "uptime_ms")
        validate_numeric(packet.get("temperature"), "temperature")
        validate_numeric(packet.get("humidity"), "humidity")
        validate_numeric(packet.get("gas_adc"), "gas_adc")
        validate_numeric(packet.get("gas_voltage_est"), "gas_voltage_est")

        accel = packet.get("accel")
        if accel is not None:
            if not isinstance(accel, dict):
                raise ValueError("Field 'accel' must be an object with {x, y, z} or null.")
            validate_numeric(accel.get("x"), "accel.x")
            validate_numeric(accel.get("y"), "accel.y")
            validate_numeric(accel.get("z"), "accel.z")

        gyro = packet.get("gyro")
        if gyro is not None:
            if not isinstance(gyro, dict):
                raise ValueError("Field 'gyro' must be an object with {x, y, z} or null.")
            validate_numeric(gyro.get("x"), "gyro.x")
            validate_numeric(gyro.get("y"), "gyro.y")
            validate_numeric(gyro.get("z"), "gyro.z")

        self._process_telemetry_packet(packet, source="wifi", client_ip=client_ip)

        return {
            "status": "success",
            "message": "Telemetry ingested successfully",
            "source": "wifi",
            "uptime_ms": packet.get("uptime_ms"),
            "timestamp": time.time()
        }

    def _run_db_writer(self):
        """Asynchronous worker that batches queue entries into SQLite."""
        while self.is_running:
            try:
                # Wait for at least one record
                item = self.db_queue.get(timeout=1.0)
                batch = [item]

                # Pull any additional pending items up to 50
                while len(batch) < 50:
                    try:
                        batch.append(self.db_queue.get_nowait())
                    except queue.Empty:
                        break

                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.executemany("""
                        INSERT INTO sensor_telemetry (
                            timestamp, uptime_ms, dht_status, mpu_status,
                            temperature, humidity, accel_x, accel_y, accel_z,
                            gyro_x, gyro_y, gyro_z, gas_adc, gas_voltage, emergency_button
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, batch)
                    conn.commit()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"SQLite batch write error: {e}")
                time.sleep(1.0)

    def get_telemetry(self):
        """Thread-safe snapshot of the latest telemetry state with stale detection."""
        with self.lock:
            now = time.time()

            # Dynamic stale check: if no new data has been ingested in > 5.0 seconds
            if self.data["last_update"] is not None:
                age = now - self.data["last_update"]
                if age > 5.0:
                    self.data["connected"] = False
                    self.data["temperature_alert"]["sensor_status"] = "stale"
                    if self.temp_alert_active:
                        self.data["temperature_alert"]["status"] = "ALERT"
                    else:
                        self.data["temperature_alert"]["status"] = "STALE"

                    self.data["gas_alert"]["sensor_status"] = "stale"
                    self.data["gas_response"]["status"] = "STALE"
                    self.data["gas_response"]["baseline_capture_ready"] = False
                    self.data["gas_response"]["baseline_capture_reason"] = "Baseline capture requires fresh sensor readings."
                    if self.gas_alert_active:
                        self.data["gas_alert"]["status"] = self.gas_alert_level
                    else:
                        self.data["gas_alert"]["status"] = "STALE"

                    if self.active_source == "wifi":
                        self.data["status"] = f"Wi-Fi Stale (Last seen {int(age)}s ago)"
                    elif self.active_source == "serial":
                        self.data["status"] = f"Serial Stale (Last seen {int(age)}s ago)"
                    else:
                        self.data["status"] = f"Disconnected (Last seen {int(age)}s ago)"
            elif not self.data["connected"]:
                self.data["temperature_alert"]["sensor_status"] = "disconnected"
                self.data["gas_alert"]["sensor_status"] = "disconnected"
                if not self.gas_alert_active:
                    self.data["gas_alert"]["status"] = "UNKNOWN"

                if self.source_mode == "wifi":
                    self.data["status"] = "Waiting for Wi-Fi Telemetry"
                elif self.source_mode == "serial":
                    self.data["status"] = f"Connecting to {self.port}..."
                else:
                    self.data["status"] = "Waiting for Telemetry (Auto)"

            snapshot = dict(self.data)
            snapshot["temperature_alert"] = dict(self.data["temperature_alert"])
            snapshot["gas_alert"] = dict(self.data["gas_alert"])
            snapshot["gas_response"] = dict(self.data["gas_response"])
            snapshot["accel"] = dict(self.data["accel"]) if self.data["accel"] else None
            snapshot["gyro"] = dict(self.data["gyro"]) if self.data["gyro"] else None
            if self.data.get("motion") and isinstance(self.data["motion"], dict):
                snapshot["motion"] = dict(self.data["motion"])
            if self.data.get("motion_analysis") and isinstance(self.data["motion_analysis"], dict):
                snapshot["motion_analysis"] = dict(self.data["motion_analysis"])
            if self.data.get("diagnostics") and isinstance(self.data["diagnostics"], dict):
                snapshot["diagnostics"] = dict(self.data["diagnostics"])
            snapshot["raw_log"] = list(self.data["raw_log"])
            return snapshot

    def get_gas_config(self):
        """Return configurable gas alert thresholds with explicit units."""
        with self.lock:
            baseline = self.gas_baseline
            return {
                "warn_threshold_adc": self.GAS_ALERT_WARN_ADC,
                "warn_threshold_voltage": round((self.GAS_ALERT_WARN_ADC / 4095.0) * 3.3, 3),
                "crit_threshold_adc": self.GAS_ALERT_CRIT_ADC,
                "crit_threshold_voltage": round((self.GAS_ALERT_CRIT_ADC / 4095.0) * 3.3, 3),
                "clear_warn_adc": self.GAS_CLEAR_WARN_ADC,
                "clear_warn_voltage": round((self.GAS_CLEAR_WARN_ADC / 4095.0) * 3.3, 3),
                "clear_crit_adc": self.GAS_CLEAR_CRIT_ADC,
                "clear_crit_voltage": round((self.GAS_CLEAR_CRIT_ADC / 4095.0) * 3.3, 3),
                "trend_window_seconds": self.GAS_RESPONSE_WINDOW_SECONDS,
                "trend_deadband_adc": self.GAS_RESPONSE_TREND_DEADBAND_ADC,
                "baseline_max_spread_adc": self.GAS_BASELINE_MAX_SPREAD_ADC,
                "baseline_adc": baseline["baseline_adc"] if baseline else None,
                "baseline_captured_at": baseline["captured_at"] if baseline else None,
                "baseline_operator_identity": baseline["operator_identity"] if baseline else None,
                "disclaimer": "PROVISIONAL DEMONSTRATION THRESHOLDS. NOT CERTIFIED LIFE-SAFETY DETECTOR."
            }

    def update_gas_config(self, updates):
        """Update configurable gas alert thresholds with validation."""
        with self.lock:
            if "warn_threshold_adc" in updates:
                w = int(updates["warn_threshold_adc"])
                if 100 <= w <= 3500:
                    self.GAS_ALERT_WARN_ADC = w
            if "clear_warn_adc" in updates:
                cw = int(updates["clear_warn_adc"])
                if 50 <= cw < self.GAS_ALERT_WARN_ADC:
                    self.GAS_CLEAR_WARN_ADC = cw
            if "crit_threshold_adc" in updates:
                c = int(updates["crit_threshold_adc"])
                if self.GAS_ALERT_WARN_ADC < c <= 4095:
                    self.GAS_ALERT_CRIT_ADC = c
            if "clear_crit_adc" in updates:
                cc = int(updates["clear_crit_adc"])
                if self.GAS_ALERT_WARN_ADC <= cc < self.GAS_ALERT_CRIT_ADC:
                    self.GAS_CLEAR_CRIT_ADC = cc

            if "trend_window_seconds" in updates:
                window = float(updates["trend_window_seconds"])
                if math.isfinite(window) and 5 <= window <= 120:
                    self.GAS_RESPONSE_WINDOW_SECONDS = window
            if "trend_deadband_adc" in updates:
                deadband = int(updates["trend_deadband_adc"])
                if 0 <= deadband <= 250:
                    self.GAS_RESPONSE_TREND_DEADBAND_ADC = deadband
            if "baseline_max_spread_adc" in updates:
                spread = int(updates["baseline_max_spread_adc"])
                if 1 <= spread <= 250:
                    self.GAS_BASELINE_MAX_SPREAD_ADC = spread

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE gas_response_config
                    SET trend_window_seconds = ?, trend_deadband_adc = ?, baseline_max_spread_adc = ?
                    WHERE id = 1
                """, (
                    self.GAS_RESPONSE_WINDOW_SECONDS,
                    self.GAS_RESPONSE_TREND_DEADBAND_ADC,
                    self.GAS_BASELINE_MAX_SPREAD_ADC,
                ))
                conn.commit()
            return self.get_gas_config()

    def get_motion_config(self):
        """Return current experimental motion analyzer thresholds."""
        with self.lock:
            return self.motion_analyzer.get_config()

    def update_motion_config(self, updates):
        """Update experimental motion analyzer thresholds with safety validation."""
        with self.lock:
            return self.motion_analyzer.update_config(updates)

    def get_history(self, limit=60):
        """Fetch recent historical readings for graphs/history table."""
        limit = min(max(1, int(limit)), 500)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM sensor_telemetry
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                # Return in chronological order
                return [dict(r) for r in reversed(rows)]
        except Exception as e:
            logger.error(f"Error querying history from SQLite: {e}")
            return []
