import os
import shutil
import tempfile
import time
import unittest

from serial_reader import SerialReader


def make_packet(gas_adc=100, mq_status="ok"):
    return {
        "uptime_ms": 1000,
        "dht_status": "ok",
        "mpu_status": "ok",
        "temperature": 25.0,
        "humidity": 50.0,
        "gas_adc": gas_adc,
        "gas_voltage_est": round(gas_adc / 4095.0 * 3.3, 3) if gas_adc is not None else None,
        "mq_status": mq_status,
        "emergency_button": "RELEASED",
        "accel": {"x": 0.0, "y": 0.0, "z": 9.81},
        "gyro": {"x": 0.0, "y": 0.0, "z": 0.0},
        "buzzer": "STANDBY",
        "buzzer_mode": "OFF",
    }


class GasResponseTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sentinel_x.db")
        self.reader = SerialReader(port="COM_TEST", db_path=self.db_path)
        self.reader.source_mode = "wifi"

    def tearDown(self):
        self.reader.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def send(self, gas_adc=100, mq_status="ok"):
        self.reader._process_telemetry_packet(make_packet(gas_adc, mq_status), source="wifi")
        return self.reader.get_telemetry()["gas_response"]

    def seed_stable_samples(self, adc_values=(100, 101, 100, 102, 101)):
        now = time.time()
        self.reader.gas_response_samples.clear()
        for index, adc in enumerate(adc_values):
            self.reader.gas_response_samples.append((now - 5 + index, adc))

    def test_valid_reading_exposes_derived_indicators(self):
        response = self.send(gas_adc=200)

        self.assertEqual(response["status"], "VALID")
        self.assertEqual(response["response_level"], "NORMAL")
        self.assertEqual(response["peak_adc_60s"], 200)
        self.assertEqual(response["trend"], "STABLE")
        self.assertIsNone(response["baseline_adc"])
        self.assertIsNone(response["relative_change_percent"])

    def test_trend_and_peak_use_recent_valid_samples(self):
        now = time.time()
        self.reader.gas_response_samples.extend([
            (now - 9, 100),
            (now - 8, 102),
            (now - 3, 130),
            (now - 2, 132),
            (now - 70, 4000),
        ])

        response = self.send(gas_adc=135)

        self.assertEqual(response["trend"], "RISING")
        self.assertEqual(response["peak_adc_60s"], 135)

    def test_relative_change_uses_captured_baseline(self):
        self.seed_stable_samples((100, 101, 100, 102, 101))
        self.send(gas_adc=101)
        self.reader.capture_gas_baseline("operator-1")

        response = self.send(gas_adc=111)

        self.assertAlmostEqual(response["relative_change_percent"], ((111 - 101) / 101) * 100, places=1)

    def test_out_of_range_adc_is_fault_and_never_an_alert_trigger(self):
        response = self.send(gas_adc=5000)
        telemetry = self.reader.get_telemetry()

        self.assertEqual(response["status"], "SENSOR FAULT")
        self.assertEqual(telemetry["gas_alert"]["level"], "NORMAL")
        self.assertIsNone(response["peak_adc_60s"])

    def test_baseline_capture_requires_valid_stable_warm_sensor(self):
        self.send(gas_adc=100, mq_status="warming_up")
        with self.assertRaises(ValueError):
            self.reader.capture_gas_baseline("operator-1")

        self.seed_stable_samples()
        response = self.send(gas_adc=102)
        self.assertTrue(response["baseline_capture_ready"], response["baseline_capture_reason"])
        baseline = self.reader.capture_gas_baseline("operator-1")
        self.assertEqual(baseline["baseline_adc"], 101)
        self.assertEqual(baseline["operator_identity"], "operator-1")

    def test_baseline_persists_and_reset_is_audited(self):
        self.seed_stable_samples((200, 201, 200, 201, 200))
        self.send(gas_adc=200)
        captured = self.reader.capture_gas_baseline("shift-lead")
        self.reader.stop()

        restored = SerialReader(port="COM_TEST", db_path=self.db_path)
        self.assertEqual(restored.get_telemetry()["gas_response"]["baseline_adc"], 200)
        reset = restored.reset_gas_baseline("shift-lead")
        events = restored.get_gas_baseline_events()
        restored.stop()

        self.assertEqual(reset["baseline_adc"], None)
        self.assertEqual(events[0]["event_type"], "RESET")
        self.assertEqual(events[1]["baseline_adc"], captured["baseline_adc"])

    def test_zero_baseline_never_divides_by_zero(self):
        self.seed_stable_samples((0, 0, 0, 0, 0))
        self.send(gas_adc=0)
        self.reader.capture_gas_baseline("operator-1")

        response = self.send(gas_adc=50)

        self.assertEqual(response["baseline_adc"], 0)
        self.assertIsNone(response["relative_change_percent"])

    def test_trend_settings_are_configurable_and_persistent(self):
        config = self.reader.update_gas_config({
            "trend_window_seconds": 20,
            "trend_deadband_adc": 12,
        })
        self.reader.stop()

        restored = SerialReader(port="COM_TEST", db_path=self.db_path)
        restored_config = restored.get_gas_config()
        restored.stop()

        self.assertEqual(config["trend_window_seconds"], 20)
        self.assertEqual(restored_config["trend_window_seconds"], 20)
        self.assertEqual(restored_config["trend_deadband_adc"], 12)


if __name__ == "__main__":
    unittest.main(verbosity=2)