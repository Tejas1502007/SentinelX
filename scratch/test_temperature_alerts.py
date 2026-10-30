import os
import sys
import time
import tempfile
import sqlite3
import unittest

# Ensure project root is in python path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))
sys.path.insert(0, r"c:\Users\Tejas Badgujar\Desktop\Sentinel X")

from serial_reader import SerialReader


class TestTemperatureAlerts(unittest.TestCase):
    def setUp(self):
        # Use an isolated temporary SQLite database for clean testing
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.reader = SerialReader(port="COM99", baudrate=115200, db_path=self.temp_db_path)
        # Avoid starting hardware threads
        self.reader.source_mode = "wifi"

    def tearDown(self):
        self.reader.stop()
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    def test_1_below_and_boundary_thresholds(self):
        """Test temperatures below 30°C and at boundaries 30°C and 31°C do not trigger an alert."""
        # 29.5°C -> Below 30°C (Normal)
        self.reader.ingest_telemetry({
            "uptime_ms": 1000, "dht_status": "ok", "temperature": 29.5, "humidity": 55.0,
            "emergency_button": "RELEASED", "mpu_status": "ok"
        })
        telem = self.reader.get_telemetry()
        alert = telem["temperature_alert"]
        self.assertFalse(alert["active"], "Alert should not be active at 29.5°C")
        self.assertEqual(alert["status"], "NORMAL")
        self.assertEqual(len(self.reader.get_alerts()), 0)

        # 30.0°C -> At clear boundary (Normal)
        self.reader.ingest_telemetry({
            "uptime_ms": 2000, "dht_status": "ok", "temperature": 30.0, "humidity": 55.0,
            "emergency_button": "RELEASED", "mpu_status": "ok"
        })
        telem = self.reader.get_telemetry()
        alert = telem["temperature_alert"]
        self.assertFalse(alert["active"], "Alert should not be active at 30.0°C")
        self.assertEqual(alert["status"], "NORMAL")
        self.assertEqual(len(self.reader.get_alerts()), 0)

        # 31.0°C -> Boundary: strictly > 31.0°C is required to trigger
        self.reader.ingest_telemetry({
            "uptime_ms": 3000, "dht_status": "ok", "temperature": 31.0, "humidity": 55.0,
            "emergency_button": "RELEASED", "mpu_status": "ok"
        })
        telem = self.reader.get_telemetry()
        alert = telem["temperature_alert"]
        self.assertFalse(alert["active"], "Alert should not trigger at exactly 31.0°C (requires > 31.0°C)")
        self.assertEqual(alert["status"], "NORMAL")
        self.assertEqual(len(self.reader.get_alerts()), 0)

    def test_2_trigger_and_deduplication(self):
        """Test trigger strictly above 31.0°C, and ensure subsequent updates do not duplicate events."""
        # 31.1°C -> Strictly above 31.0°C (TRIGGER)
        self.reader.ingest_telemetry({
            "uptime_ms": 4000, "dht_status": "ok", "temperature": 31.1, "humidity": 55.0,
            "emergency_button": "RELEASED", "mpu_status": "ok"
        })
        telem = self.reader.get_telemetry()
        alert = telem["temperature_alert"]
        self.assertTrue(alert["active"], "Alert must be active at 31.1°C")
        self.assertEqual(alert["status"], "ALERT")

        alerts = self.reader.get_alerts()
        self.assertEqual(len(alerts), 1, "Exactly 1 alert event should be recorded")
        self.assertEqual(alerts[0]["event_type"], "TRIGGERED")
        self.assertEqual(alerts[0]["alert_type"], "TEMPERATURE_ALERT")
        self.assertEqual(alerts[0]["sensor_value"], 31.1)
        self.assertEqual(alerts[0]["threshold_value"], 31.0)

        # 32.5°C -> Still above 31.0°C (Deduplication check)
        self.reader.ingest_telemetry({
            "uptime_ms": 5000, "dht_status": "ok", "temperature": 32.5, "humidity": 55.0,
            "emergency_button": "RELEASED", "mpu_status": "ok"
        })
        telem = self.reader.get_telemetry()
        alert = telem["temperature_alert"]
        self.assertTrue(alert["active"])
        self.assertEqual(alert["status"], "ALERT")

        alerts_after = self.reader.get_alerts()
        self.assertEqual(len(alerts_after), 1, "Duplicate event must NOT be created when alert condition continues")

    def test_3_hysteresis_band(self):
        """Test that temperature dropping to 30.5°C (between 30°C and 31°C) keeps alert active."""
        # Trigger alert with 33.0°C
        self.reader.ingest_telemetry({
            "uptime_ms": 1000, "dht_status": "ok", "temperature": 33.0, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        self.assertTrue(self.reader.get_telemetry()["temperature_alert"]["active"])

        # Temperature drops to 30.8°C (in hysteresis band) -> Still ALERT
        self.reader.ingest_telemetry({
            "uptime_ms": 2000, "dht_status": "ok", "temperature": 30.8, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        alert = self.reader.get_telemetry()["temperature_alert"]
        self.assertTrue(alert["active"], "Alert must remain active at 30.8°C due to hysteresis")
        self.assertEqual(alert["status"], "ALERT")
        self.assertEqual(len(self.reader.get_alerts()), 1)

        # Temperature drops to 30.1°C (still above 30.0°C clear threshold) -> Still ALERT
        self.reader.ingest_telemetry({
            "uptime_ms": 3000, "dht_status": "ok", "temperature": 30.1, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        alert = self.reader.get_telemetry()["temperature_alert"]
        self.assertTrue(alert["active"], "Alert must remain active at 30.1°C")
        self.assertEqual(alert["status"], "ALERT")
        self.assertEqual(len(self.reader.get_alerts()), 1)

    def test_4_invalid_and_stale_never_clears_alert(self):
        """Test that missing, invalid, or stale data never clears an alert or marks safe."""
        # Trigger alert with 32.0°C
        self.reader.ingest_telemetry({
            "uptime_ms": 1000, "dht_status": "ok", "temperature": 32.0, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        self.assertTrue(self.reader.get_telemetry()["temperature_alert"]["active"])

        # Packet with DHT error / missing temperature
        self.reader.ingest_telemetry({
            "uptime_ms": 2000, "dht_status": "error", "temperature": None, "humidity": None,
            "emergency_button": "RELEASED"
        })
        alert = self.reader.get_telemetry()["temperature_alert"]
        self.assertTrue(alert["active"], "Missing/invalid temperature reading must NEVER clear an active alert")
        self.assertEqual(alert["status"], "ALERT")
        self.assertEqual(alert["sensor_status"], "error")
        self.assertEqual(len(self.reader.get_alerts()), 1, "No clear event should be recorded on error")

    def test_5_clear_threshold_and_hysteresis_recovery(self):
        """Test that alert clears when temperature reaches <= 30.0°C and clear event is recorded once."""
        # Trigger
        self.reader.ingest_telemetry({
            "uptime_ms": 1000, "dht_status": "ok", "temperature": 32.0, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        self.assertEqual(len(self.reader.get_alerts()), 1)

        # Clear at exactly 30.0°C
        self.reader.ingest_telemetry({
            "uptime_ms": 2000, "dht_status": "ok", "temperature": 30.0, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        alert = self.reader.get_telemetry()["temperature_alert"]
        self.assertFalse(alert["active"], "Alert must clear when temperature reaches <= 30.0°C")
        self.assertEqual(alert["status"], "NORMAL")

        alerts = self.reader.get_alerts()
        self.assertEqual(len(alerts), 2, "Must record exactly 1 clear event")
        self.assertEqual(alerts[0]["event_type"], "CLEARED")
        self.assertEqual(alerts[0]["sensor_value"], 30.0)
        self.assertEqual(alerts[0]["threshold_value"], 30.0)

        # Subsequent readings at 28.0°C must not generate duplicate clear events
        self.reader.ingest_telemetry({
            "uptime_ms": 3000, "dht_status": "ok", "temperature": 28.0, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        self.assertEqual(len(self.reader.get_alerts()), 2, "Duplicate clear event must NOT be recorded")

    def test_6_buzzer_separation(self):
        """Confirm temperature alert status is kept strictly separate from buzzer status."""
        self.reader.ingest_telemetry({
            "uptime_ms": 1000, "dht_status": "ok", "temperature": 35.0, "humidity": 55.0,
            "emergency_button": "RELEASED", "buzzer": "STANDBY"
        })
        telem = self.reader.get_telemetry()
        self.assertTrue(telem["temperature_alert"]["active"])
        self.assertEqual(telem["buzzer"], "STANDBY", "Buzzer must remain STANDBY when temperature is high unless SOS pressed")

    def test_7_sqlite_persistence_and_acknowledgement(self):
        """Test SQLite persistence across restarts and alert acknowledgement."""
        self.reader.ingest_telemetry({
            "uptime_ms": 1000, "dht_status": "ok", "temperature": 32.0, "humidity": 55.0,
            "emergency_button": "RELEASED"
        })
        alerts = self.reader.get_alerts()
        self.assertEqual(len(alerts), 1)
        alert_id = alerts[0]["id"]
        self.assertFalse(alerts[0]["acknowledged"])

        # Acknowledge alert
        success = self.reader.acknowledge_alert(alert_id)
        self.assertTrue(success)
        alerts_acked = self.reader.get_alerts()
        self.assertTrue(alerts_acked[0]["acknowledged"])

        # Simulate backend restart with a new SerialReader instance on the same DB
        new_reader = SerialReader(port="COM99", baudrate=115200, db_path=self.temp_db_path)
        persisted_alerts = new_reader.get_alerts()
        self.assertEqual(len(persisted_alerts), 1)
        self.assertEqual(persisted_alerts[0]["event_type"], "TRIGGERED")
        self.assertEqual(persisted_alerts[0]["sensor_value"], 32.0)
        self.assertTrue(persisted_alerts[0]["acknowledged"])
        # Alert state was restored from DB
        self.assertTrue(new_reader.temp_alert_active)
        self.assertEqual(new_reader.temp_alert_status, "ALERT")
        new_reader.stop()


    def test_8_buzzer_policy_and_sos_priority(self):
        """Test telemetry handling of buzzer policy: SOS priority, sound_active, and timeout."""
        # Scenario A: New high temperature alert sounds buzzer (sound_active=True, buzzer_mode=TEMP_ALERT)
        self.reader.ingest_telemetry({
            "uptime_ms": 1000, "dht_status": "ok", "temperature": 32.5, "humidity": 55.0,
            "emergency_button": "RELEASED", "buzzer": "ACTIVE", "buzzer_mode": "TEMP_ALERT",
            "temperature_alert": {"status": "ALERT", "active": True, "sound_active": True}
        })
        telem = self.reader.get_telemetry()
        self.assertTrue(telem["temperature_alert"]["active"])
        self.assertTrue(telem["temperature_alert"]["sound_active"])
        self.assertEqual(telem["buzzer"], "ACTIVE")
        self.assertEqual(telem.get("buzzer_mode"), "TEMP_ALERT")

        # Scenario B: SOS button pressed during temperature alert -> SOS takes absolute priority
        self.reader.ingest_telemetry({
            "uptime_ms": 5000, "dht_status": "ok", "temperature": 32.5, "humidity": 55.0,
            "emergency_button": "PRESSED", "buzzer": "ACTIVE", "buzzer_mode": "SOS",
            "temperature_alert": {"status": "ALERT", "active": True, "sound_active": True}
        })
        telem = self.reader.get_telemetry()
        self.assertEqual(telem["emergency_button"], "PRESSED")
        self.assertEqual(telem["buzzer"], "ACTIVE")
        self.assertEqual(telem.get("buzzer_mode"), "SOS")

        # Scenario C: SOS released after 60s timeout -> temp buzzer has timed out, buzzer becomes STANDBY
        # while temperature alert remains ACTIVE on dashboard
        self.reader.ingest_telemetry({
            "uptime_ms": 70000, "dht_status": "ok", "temperature": 32.5, "humidity": 55.0,
            "emergency_button": "RELEASED", "buzzer": "STANDBY", "buzzer_mode": "OFF",
            "temperature_alert": {"status": "ALERT", "active": True, "sound_active": False}
        })
        telem = self.reader.get_telemetry()
        self.assertTrue(telem["temperature_alert"]["active"], "Dashboard alert must stay active after buzzer timeout")
        self.assertFalse(telem["temperature_alert"]["sound_active"], "Buzzer sound must be silenced after 60s")
        self.assertEqual(telem["buzzer"], "STANDBY")

        # Scenario D: Temperature normalizes <= 30.0°C -> alert clears completely
        self.reader.ingest_telemetry({
            "uptime_ms": 75000, "dht_status": "ok", "temperature": 29.5, "humidity": 55.0,
            "emergency_button": "RELEASED", "buzzer": "STANDBY", "buzzer_mode": "OFF",
            "temperature_alert": {"status": "NORMAL", "active": False, "sound_active": False}
        })
        telem = self.reader.get_telemetry()
        self.assertFalse(telem["temperature_alert"]["active"])
        self.assertEqual(telem["temperature_alert"]["status"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
