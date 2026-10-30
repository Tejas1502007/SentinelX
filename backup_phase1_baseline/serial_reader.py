import json
import logging
import queue
import sqlite3
import threading
import time
import os
import serial
import serial.tools.list_ports

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
        self.lock = threading.Lock()

        # Thread-safe telemetry state
        self.data = {
            "connected": False,
            "status": "Initializing",
            "port": self.port,
            "baudrate": self.baudrate,
            "last_update": None,
            "uptime_ms": None,
            "dht_status": "disconnected",
            "mpu_status": "disconnected",
            "temperature": None,
            "humidity": None,
            "accel": {"x": 0.0, "y": 0.0, "z": 0.0},
            "gyro": {"x": 0.0, "y": 0.0, "z": 0.0},
            "gas_adc": None,
            "gas_voltage_est": None,
            "emergency_button": "RELEASED",
            "raw_log": []
        }

        self._init_database()

    def _init_database(self):
        """Initialize the SQLite schema if not already present."""
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
                conn.commit()
            logger.info(f"SQLite database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize SQLite database: {e}")

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
        """Continuous background serial polling with auto-reconnection."""
        while self.is_running:
            if not self.serial_conn or not self.serial_conn.is_open:
                try:
                    with self.lock:
                        self.data["status"] = f"Connecting to {self.port}..."
                    self.serial_conn = serial.Serial(
                        port=self.port,
                        baudrate=self.baudrate,
                        timeout=1.5
                    )
                    self.serial_conn.reset_input_buffer()
                    with self.lock:
                        self.data["connected"] = True
                        self.data["status"] = "Connected"
                        self._append_log(f"Connected to {self.port} @ {self.baudrate} baud.")
                    logger.info(f"Successfully opened serial port {self.port}.")
                except (serial.SerialException, FileNotFoundError, PermissionError) as e:
                    with self.lock:
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

                self._parse_line(line)

            except (serial.SerialException, OSError) as e:
                logger.warning(f"Hardware disconnected on {self.port}: {e}")
                with self.lock:
                    self.data["connected"] = False
                    self.data["status"] = "Hardware Disconnected"
                    self._append_log(f"Device disconnected: {e}")
                self._disconnect_hardware()
                time.sleep(2.0)
            except Exception as e:
                logger.error(f"Unexpected read loop error: {e}")
                time.sleep(0.5)

    def _parse_line(self, line):
        """Parse incoming JSON packet and enqueue for SQLite persistence."""
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

        now = time.time()

        with self.lock:
            self.data["last_update"] = now
            self.data["uptime_ms"] = packet.get("uptime_ms")
            self.data["dht_status"] = packet.get("dht_status", "unknown")
            self.data["mpu_status"] = packet.get("mpu_status", "unknown")

            self.data["temperature"] = packet.get("temperature")
            self.data["humidity"] = packet.get("humidity")

            accel = packet.get("accel")
            if isinstance(accel, dict):
                self.data["accel"] = {
                    "x": accel.get("x", 0.0),
                    "y": accel.get("y", 0.0),
                    "z": accel.get("z", 0.0)
                }
            else:
                self.data["accel"] = None

            gyro = packet.get("gyro")
            if isinstance(gyro, dict):
                self.data["gyro"] = {
                    "x": gyro.get("x", 0.0),
                    "y": gyro.get("y", 0.0),
                    "z": gyro.get("z", 0.0)
                }
            else:
                self.data["gyro"] = None

            # New: Gas & Emergency Button
            self.data["gas_adc"] = packet.get("gas_adc")
            self.data["gas_voltage_est"] = packet.get("gas_voltage_est")
            self.data["emergency_button"] = packet.get("emergency_button", "RELEASED")

        # Asynchronously enqueue record for DB write without blocking serial read
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
            pass  # Drop oldest or skip if queue is saturated

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
        """Thread-safe snapshot of the latest telemetry state."""
        with self.lock:
            snapshot = dict(self.data)
            snapshot["accel"] = dict(self.data["accel"]) if self.data["accel"] else None
            snapshot["gyro"] = dict(self.data["gyro"]) if self.data["gyro"] else None
            snapshot["raw_log"] = list(self.data["raw_log"])
            return snapshot

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
