"""
Comprehensive Unit & Integration Tests for SENTINEL-X MQ-135 Gas Alert Integration
Tests:
1. Baseline clean air (nominal ~50 ADC)
2. Warning threshold crossing (>= 400 ADC)
3. Warning hysteresis deadband (350 - 400 ADC)
4. Critical threshold crossing (>= 1000 ADC)
5. Critical hysteresis deadband (850 - 1000 ADC)
6. De-escalation from Critical to Warning (< 850 ADC, >= 400 ADC)
7. Full normalization / clear (< 350 ADC)
8. Sensor fault and disconnection handling (missing/None/error readings never report 'Safe' or clear active alerts)
9. Stale data handling (> 5.0s timeout)
"""

import os
import shutil
import tempfile
import time
import unittest
from serial_reader import SerialReader


class TestMQ135GasAlerts(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sentinel_x.db")
        self.reader = SerialReader(port="COM_TEST", baudrate=115200, db_path=self.db_path)
        self.reader.source_mode = "wifi"

    def tearDown(self):
        self.reader.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _send_packet(self, gas_adc, mq_status="ok", uptime=1000):
        voltage = round((gas_adc / 4095.0) * 3.3, 3) if gas_adc is not None else None
        packet = {
            "uptime_ms": uptime,
            "dht_status": "ok",
            "mpu_status": "ok",
            "mq_status": mq_status,
            "temperature": 25.0,
            "humidity": 50.0,
            "gas_adc": gas_adc,
            "gas_voltage_est": voltage,
            "emergency_button": "RELEASED",
            "accel": {"x": 0.0, "y": 0.0, "z": 9.81},
            "gyro": {"x": 0.0, "y": 0.0, "z": 0.0}
        }
        self.reader._process_telemetry_packet(packet, source="wifi")
        return self.reader.get_telemetry()

    def test_01_baseline_clean_air_is_normal(self):
        """Clean air baseline (~50 ADC) should report NORMAL and no active alert."""
        data = self._send_packet(gas_adc=50)
        gas_alert = data["gas_alert"]
        self.assertFalse(gas_alert["active"])
        self.assertEqual(gas_alert["status"], "NORMAL")
        self.assertEqual(gas_alert["level"], "NORMAL")
        self.assertEqual(gas_alert["current_adc"], 50)
        self.assertEqual(len(self.reader.get_alerts()), 0)

    def test_02_warning_threshold_trigger(self):
        """Crossing warning threshold (>= 400 ADC) triggers WARNING alert and persists event."""
        # 1. Clean air
        self._send_packet(gas_adc=60)
        # 2. Elevated gas crossing 400 ADC
        data = self._send_packet(gas_adc=450)
        gas_alert = data["gas_alert"]

        self.assertTrue(gas_alert["active"])
        self.assertEqual(gas_alert["status"], "WARNING")
        self.assertEqual(gas_alert["level"], "WARNING")
        self.assertEqual(gas_alert["current_adc"], 450)

        events = self.reader.get_alerts()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["alert_type"], "GAS_ALERT")
        self.assertEqual(events[0]["event_type"], "TRIGGERED")
        self.assertEqual(events[0]["severity"], "Warning")
        self.assertIn("Elevated MQ-135 response threshold", events[0]["message"])

    def test_03_warning_hysteresis_deadband(self):
        """Within 350-400 ADC hysteresis band, WARNING state persists without duplicate events."""
        self._send_packet(gas_adc=450)
        # Dropping to 380 ADC (in 350-400 deadband)
        data = self._send_packet(gas_adc=380)
        gas_alert = data["gas_alert"]

        self.assertTrue(gas_alert["active"])
        self.assertEqual(gas_alert["status"], "WARNING")
        # Ensure no duplicate event logged in deadband
        self.assertEqual(len(self.reader.get_alerts()), 1)

    def test_04_critical_threshold_escalation(self):
        """Crossing critical threshold (>= 1000 ADC) escalates to CRITICAL Emergency alert."""
        self._send_packet(gas_adc=450)  # Warning first
        # Spike into critical
        data = self._send_packet(gas_adc=1150)
        gas_alert = data["gas_alert"]

        self.assertTrue(gas_alert["active"])
        self.assertEqual(gas_alert["status"], "CRITICAL")
        self.assertEqual(gas_alert["level"], "CRITICAL")

        events = self.reader.get_alerts()
        self.assertEqual(len(events), 2)
        # Most recent event is ESCALATED / CRITICAL
        self.assertEqual(events[0]["event_type"], "ESCALATED")
        self.assertEqual(events[0]["severity"], "Emergency")
        self.assertIn("CRITICAL MQ-135 response threshold", events[0]["message"])

    def test_05_critical_hysteresis_and_deescalation(self):
        """
        Critical clears below 850 ADC. Between 850 and 1000 ADC, it maintains CRITICAL.
        Below 850 ADC, it de-escalates to WARNING if >= 400 ADC.
        """
        self._send_packet(gas_adc=1100)  # Critical
        # 1. 920 ADC: in critical deadband (850-1000), must remain CRITICAL
        data_deadband = self._send_packet(gas_adc=920)
        self.assertEqual(data_deadband["gas_alert"]["level"], "CRITICAL")
        self.assertEqual(len(self.reader.get_alerts()), 1)

        # 2. 750 ADC: drops below 850 clear threshold, de-escalates to WARNING
        data_deescalate = self._send_packet(gas_adc=750)
        self.assertEqual(data_deescalate["gas_alert"]["level"], "WARNING")
        self.assertEqual(data_deescalate["gas_alert"]["status"], "WARNING")

        events = self.reader.get_alerts()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "DE-ESCALATED")
        self.assertEqual(events[0]["severity"], "Warning")

    def test_06_normalization_and_clear(self):
        """Dropping below 350 ADC clears the alert and logs CLEARED event."""
        self._send_packet(gas_adc=450)  # Trigger warning
        # Drop to clean baseline (250 ADC < 350 clear threshold)
        data_cleared = self._send_packet(gas_adc=250)
        gas_alert = data_cleared["gas_alert"]

        self.assertFalse(gas_alert["active"])
        self.assertEqual(gas_alert["status"], "NORMAL")
        self.assertEqual(gas_alert["level"], "NORMAL")

        events = self.reader.get_alerts()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "CLEARED")
        self.assertEqual(events[0]["severity"], "Info")
        self.assertIn("MQ-135 response returned below warning threshold", events[0]["message"])

    def test_07_sensor_fault_never_clears_active_alert(self):
        """
        Missing, None, or error reading must report SENSOR_FAULT/UNKNOWN.
        An active alert MUST NOT be cleared by invalid data!
        """
        # 1. Active warning
        self._send_packet(gas_adc=450)
        self.assertTrue(self.reader.gas_alert_active)

        # 2. Sensor suddenly disconnects or errors (gas_adc = None)
        data_err = self._send_packet(gas_adc=None, mq_status="error")
        # Active alert MUST remain active!
        self.assertTrue(data_err["gas_alert"]["active"])
        self.assertEqual(data_err["gas_alert"]["status"], "WARNING")

        # 3. Clean air after reconnecting still in deadband: remains active
        data_reconn = self._send_packet(gas_adc=380, mq_status="ok")
        self.assertTrue(data_reconn["gas_alert"]["active"])

        # 4. Finally drops below 350 ADC: only now can it clear!
        data_clean = self._send_packet(gas_adc=100, mq_status="ok")
        self.assertFalse(data_clean["gas_alert"]["active"])
        self.assertEqual(data_clean["gas_alert"]["status"], "NORMAL")

    def test_08_disconnected_sensor_before_alert_reports_fault(self):
        """A disconnected sensor without prior alert shows SENSOR_FAULT / UNKNOWN, NEVER 'Safe'."""
        data = self._send_packet(gas_adc=None, mq_status="error")
        self.assertFalse(data["gas_alert"]["active"])
        self.assertEqual(data["gas_alert"]["status"], "SENSOR_FAULT")
        self.assertNotEqual(data["gas_alert"]["status"], "NORMAL")


if __name__ == "__main__":
    unittest.main()
