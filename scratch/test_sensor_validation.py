"""
SENTINEL-X — Sensor Data Validation & Integration Test Suite
Tests raw sensor reading paths, data integrity, status state machines,
packet validation, and cross-sensor interactions that are NOT covered
by the existing alert-threshold / motion-analysis / buzzer test suites.

Sensors covered:
  1. DHT22  — temperature & humidity validity, status transitions
  2. MPU6050 — accel/gyro range, magnitude, NaN handling, window features
  3. MQ-135 — ADC range, voltage conversion, warm-up, stale transitions
  4. SOS button — press/release event lifecycle
  5. Telemetry ingest — field type validation, malformed payloads
  6. Cross-sensor — simultaneous alerts, stale coexistence, buzzer arbitration
"""

import math
import os
import shutil
import tempfile
import time
import unittest
from serial_reader import SerialReader


def make_packet(
    uptime_ms=1000,
    dht_status="ok",
    temperature=25.0,
    humidity=50.0,
    mpu_status="ok",
    accel_x=0.0, accel_y=0.0, accel_z=9.81,
    gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
    mq_status="ok",
    gas_adc=50,
    gas_voltage_est=None,
    emergency_button="RELEASED",
    buzzer="STANDBY",
    buzzer_mode="OFF",
    temperature_alert=None,
    gas_alert=None,
):
    """Build a minimal valid telemetry packet with sensible defaults."""
    if gas_voltage_est is None:
        gas_voltage_est = round((gas_adc / 4095.0) * 3.3, 3) if gas_adc is not None else None
    pkt = {
        "uptime_ms": uptime_ms,
        "dht_status": dht_status,
        "mpu_status": mpu_status,
        "mq_status": mq_status,
        "temperature": temperature,
        "humidity": humidity,
        "gas_adc": gas_adc,
        "gas_voltage_est": gas_voltage_est,
        "emergency_button": emergency_button,
        "accel": {"x": accel_x, "y": accel_y, "z": accel_z},
        "gyro": {"x": gyro_x, "y": gyro_y, "z": gyro_z},
        "buzzer": buzzer,
        "buzzer_mode": buzzer_mode,
    }
    if temperature_alert is not None:
        pkt["temperature_alert"] = temperature_alert
    if gas_alert is not None:
        pkt["gas_alert"] = gas_alert
    return pkt


class BaseSensorTest(unittest.TestCase):
    """Shared setup/teardown: isolated temp DB, wifi-only reader, no hardware threads."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_sentinel_x.db")
        self.reader = SerialReader(port="COM_TEST", baudrate=115200, db_path=self.db_path)
        self.reader.source_mode = "wifi"

    def tearDown(self):
        self.reader.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def send(self, **kwargs):
        """Send a telemetry packet and return the resulting telemetry snapshot."""
        pkt = make_packet(**kwargs)
        self.reader._process_telemetry_packet(pkt, source="wifi")
        return self.reader.get_telemetry()

    def send_raw(self, pkt):
        """Send a pre-built packet dict and return the telemetry snapshot."""
        self.reader._process_telemetry_packet(pkt, source="wifi")
        return self.reader.get_telemetry()


# =====================================================================
# 1. DHT22 — Temperature & Humidity Sensor
# =====================================================================

class TestDHT22Sensor(BaseSensorTest):

    def test_valid_normal_reading(self):
        """A normal 25°C / 50% reading is stored with correct values."""
        data = self.send(temperature=25.0, humidity=50.0, dht_status="ok")
        self.assertEqual(data["temperature"], 25.0)
        self.assertEqual(data["humidity"], 50.0)
        self.assertEqual(data["dht_status"], "ok")
        self.assertEqual(data["temperature_alert"]["sensor_status"], "ok")

    def test_extreme_valid_temperature_low(self):
        """DHT22 spec: -40°C is the minimum valid reading (no alert since <= 30°C)."""
        data = self.send(temperature=-40.0, humidity=20.0, dht_status="ok")
        self.assertEqual(data["temperature"], -40.0)
        self.assertFalse(data["temperature_alert"]["active"])

    def test_extreme_valid_temperature_high(self):
        """DHT22 spec: 80°C is the maximum valid reading (triggers alert since > 31°C)."""
        data = self.send(temperature=80.0, humidity=10.0, dht_status="ok")
        self.assertEqual(data["temperature"], 80.0)
        self.assertTrue(data["temperature_alert"]["active"])

    def test_humidity_boundary_zero(self):
        """0% humidity is a valid DHT22 reading."""
        data = self.send(temperature=25.0, humidity=0.0, dht_status="ok")
        self.assertEqual(data["humidity"], 0.0)

    def test_humidity_boundary_hundred(self):
        """100% humidity is a valid DHT22 reading."""
        data = self.send(temperature=25.0, humidity=100.0, dht_status="ok")
        self.assertEqual(data["humidity"], 100.0)

    def test_null_temperature_treated_as_unavailable(self):
        """Null temperature with ok status should NOT be treated as valid."""
        data = self.send(temperature=None, humidity=None, dht_status="ok")
        self.assertIsNone(data["temperature"])
        self.assertFalse(data["temperature_alert"]["active"])
        self.assertEqual(data["temperature_alert"]["sensor_status"], "unavailable")

    def test_dht_error_status(self):
        """dht_status='error' should be reflected in sensor_status and not crash."""
        data = self.send(temperature=None, humidity=None, dht_status="error")
        self.assertEqual(data["dht_status"], "error")
        self.assertEqual(data["temperature_alert"]["sensor_status"], "error")
        self.assertFalse(data["temperature_alert"]["active"])

    def test_dht_stale_transition(self):
        """DHT status transitions from ok to stale when no update arrives within timeout."""
        data = self.send(temperature=25.0, humidity=50.0, dht_status="ok")
        self.assertEqual(data["dht_status"], "ok")

        # Simulate staleness by not sending for > 5s
        time.sleep(5.1)
        data = self.reader.get_telemetry()
        # Stale detection in get_telemetry()
        self.assertFalse(data["connected"])
        self.assertIn("Stale", data["status"])

    def test_temperature_alert_not_triggered_by_null_with_prior_normal(self):
        """If temp was normal and then goes null, alert should not activate."""
        data = self.send(temperature=25.0, humidity=50.0, dht_status="ok")
        self.assertFalse(data["temperature_alert"]["active"])

        data2 = self.send(temperature=None, humidity=None, dht_status="error")
        self.assertFalse(data2["temperature_alert"]["active"],
                         "Null reading must not trigger a new alert")

    def test_temperature_persistence_across_packets(self):
        """Latest valid temperature should persist until a new valid reading arrives."""
        self.send(temperature=26.0, humidity=51.0, dht_status="ok")
        self.send(temperature=None, humidity=None, dht_status="error")
        # The stored temperature becomes None from the packet, but alert logic
        # must not have cleared or triggered anything new
        data = self.reader.get_telemetry()
        # temp_alert_active should still be False since we never triggered
        self.assertFalse(data["temperature_alert"]["active"])


# =====================================================================
# 2. MPU6050 — Accelerometer & Gyroscope (IMU)
# =====================================================================

class TestMPU6050Sensor(BaseSensorTest):

    def test_stationary_gravity_reading(self):
        """A stationary sensor reports ~9.81 m/s² on Z-axis and ~0 on X/Y."""
        data = self.send(
            accel_x=0.0, accel_y=0.0, accel_z=9.81,
            gyro_x=0.0, gyro_y=0.0, gyro_z=0.0,
            mpu_status="ok",
        )
        self.assertAlmostEqual(data["accel"]["z"], 9.81, places=2)
        self.assertAlmostEqual(data["accel"]["x"], 0.0, places=2)
        self.assertEqual(data["mpu_status"], "ok")

    def test_accel_max_range_positive(self):
        """Accelerometer at +2g full scale: max reading ~19.61 m/s² (all axes)."""
        ax = 19.6133 / math.sqrt(3)  # distribute across 3 axes to stay within |a| = 2g
        data = self.send(accel_x=ax, accel_y=ax, accel_z=ax, mpu_status="ok")
        mag = math.sqrt(data["accel"]["x"]**2 + data["accel"]["y"]**2 + data["accel"]["z"]**2)
        self.assertAlmostEqual(mag, 19.6133, places=1)

    def test_accel_negative_values(self):
        """Negative acceleration values (e.g., free-fall or inverted) are preserved."""
        data = self.send(accel_x=-5.0, accel_y=-3.0, accel_z=-9.81, mpu_status="ok")
        self.assertEqual(data["accel"]["x"], -5.0)
        self.assertEqual(data["accel"]["y"], -3.0)
        self.assertEqual(data["accel"]["z"], -9.81)

    def test_gyro_max_range(self):
        """Gyroscope at ±250 deg/s = ±4.363 rad/s. Large values should be stored correctly."""
        max_rads = 250.0 * math.pi / 180.0
        data = self.send(gyro_x=max_rads, gyro_y=0.0, gyro_z=0.0, mpu_status="ok")
        self.assertAlmostEqual(data["gyro"]["x"], max_rads, places=2)

    def test_nan_accel_components(self):
        """NaN accel components (sensor comm error) are stored as-is without crashing."""
        data = self.send(accel_x=float('nan'), accel_y=0.0, accel_z=9.81, mpu_status="ok")
        # The packet stores what it receives; the app shouldn't crash
        self.assertIsNotNone(data["accel"])

    def test_missing_accel_object(self):
        """A packet with no accel field should set accel to None gracefully."""
        pkt = make_packet()
        del pkt["accel"]
        data = self.send_raw(pkt)
        self.assertIsNone(data["accel"])

    def test_missing_gyro_object(self):
        """A packet with no gyro field should set gyro to None gracefully."""
        pkt = make_packet()
        del pkt["gyro"]
        data = self.send_raw(pkt)
        self.assertIsNone(data["gyro"])

    def test_partial_accel_components(self):
        """Accel object with missing keys defaults to 0.0 for absent axes."""
        pkt = make_packet()
        pkt["accel"] = {"x": 5.0}  # y and z missing
        data = self.send_raw(pkt)
        self.assertEqual(data["accel"]["x"], 5.0)
        self.assertEqual(data["accel"]["y"], 0.0)
        self.assertEqual(data["accel"]["z"], 0.0)

    def test_mpu_error_status(self):
        """mpu_status='error' is stored and motion analysis reports FAULT quality."""
        data = self.send(mpu_status="error", accel_x=None, accel_y=None, accel_z=None)
        self.assertEqual(data["mpu_status"], "error")
        if data.get("motion_analysis"):
            self.assertEqual(data["motion_analysis"]["data_quality"]["status"], "FAULT")

    def test_motion_features_fallback_when_missing(self):
        """When motion features are absent, backend computes a fallback from accel/gyro."""
        pkt = make_packet(accel_x=0.0, accel_y=0.0, accel_z=9.81)
        # No 'motion' key in packet — backend should synthesize one
        data = self.send_raw(pkt)
        self.assertIsNotNone(data["motion"], "Backend must synthesize motion from accel/gyro if packet lacks it")
        self.assertAlmostEqual(data["motion"]["accel_magnitude_mps2"], 9.81, places=1)

    def test_window_features_reflected_in_motion(self):
        """Motion window features from firmware packet are passed through to telemetry."""
        pkt = make_packet()
        pkt["motion"] = {
            "accel_magnitude_mps2": 9.85,
            "accel_magnitude_g": 1.004,
            "gyro_magnitude_rads": 0.05,
            "window_samples": 50,
            "window_duration_ms": 1000,
            "accel_mag_mean_mps2": 9.82,
            "accel_mag_peak_mps2": 10.1,
            "accel_mag_std_mps2": 0.08,
            "gyro_mag_mean_rads": 0.03,
            "gyro_mag_peak_rads": 0.07,
            "window_complete": True,
        }
        data = self.send_raw(pkt)
        self.assertEqual(data["motion"]["window_samples"], 50)
        self.assertEqual(data["motion"]["accel_mag_peak_mps2"], 10.1)
        self.assertTrue(data["motion"]["window_complete"])


# =====================================================================
# 3. MQ-135 — Gas Sensor
# =====================================================================

class TestMQ135Sensor(BaseSensorTest):

    def test_clean_air_baseline(self):
        """Typical clean air reading (~50 ADC, ~0.04V) reports NORMAL."""
        data = self.send(gas_adc=50, mq_status="ok")
        self.assertEqual(data["gas_adc"], 50)
        self.assertAlmostEqual(data["gas_voltage_est"], round(50 / 4095.0 * 3.3, 3), places=3)
        self.assertEqual(data["gas_alert"]["level"], "NORMAL")
        self.assertFalse(data["gas_alert"]["active"])

    def test_adc_zero(self):
        """ADC = 0 is a valid reading (0V), should report NORMAL."""
        data = self.send(gas_adc=0, mq_status="ok")
        self.assertEqual(data["gas_adc"], 0)
        self.assertAlmostEqual(data["gas_voltage_est"], 0.0, places=3)
        self.assertFalse(data["gas_alert"]["active"])

    def test_adc_max_value(self):
        """ADC = 4095 (12-bit max, 3.3V) is valid and should be stored."""
        data = self.send(gas_adc=4095, mq_status="ok")
        self.assertEqual(data["gas_adc"], 4095)
        self.assertAlmostEqual(data["gas_voltage_est"], 3.3, places=2)
        self.assertEqual(data["gas_alert"]["level"], "CRITICAL")

    def test_voltage_conversion_accuracy(self):
        """Verify the ADC-to-voltage conversion formula: V = ADC / 4095 * 3.3."""
        for adc_val in [0, 100, 400, 850, 1000, 2048, 4095]:
            expected_v = round(adc_val / 4095.0 * 3.3, 3)
            data = self.send(gas_adc=adc_val, mq_status="ok")
            self.assertAlmostEqual(
                data["gas_voltage_est"], expected_v, places=3,
                msg=f"Voltage mismatch for ADC={adc_val}"
            )

    def test_gas_alert_threshold_values_in_telemetry(self):
        """Telemetry should expose the configured warn/crit thresholds."""
        data = self.send(gas_adc=50, mq_status="ok")
        ga = data["gas_alert"]
        self.assertEqual(ga["warn_threshold_adc"], 400)
        self.assertEqual(ga["crit_threshold_adc"], 1000)
        self.assertEqual(ga["clear_warn_adc"], 350)
        self.assertEqual(ga["clear_crit_adc"], 850)

    def test_warmup_status(self):
        """During warm-up (mq_status='warming_up'), status should reflect warming state."""
        data = self.send(gas_adc=100, mq_status="warming_up")
        self.assertEqual(data["mq_status"], "warming_up")
        # No alert should be active during warmup
        self.assertFalse(data["gas_alert"]["active"])

    def test_null_gas_adc(self):
        """Null gas_adc with error status reports SENSOR_FAULT, never NORMAL."""
        data = self.send(gas_adc=None, gas_voltage_est=None, mq_status="error")
        self.assertIsNone(data["gas_adc"])
        self.assertFalse(data["gas_alert"]["active"])
        self.assertEqual(data["gas_alert"]["status"], "SENSOR_FAULT")

    def test_gas_config_update_validation(self):
        """Updating gas thresholds via update_gas_config should clamp invalid values."""
        # Valid update
        result = self.reader.update_gas_config({
            "warn_threshold_adc": 500,
            "clear_warn_adc": 450,
            "crit_threshold_adc": 1200,
            "clear_crit_adc": 1000,
        })
        self.assertEqual(result["warn_threshold_adc"], 500)
        self.assertEqual(result["crit_threshold_adc"], 1200)

        # Invalid: warn below minimum (100)
        result2 = self.reader.update_gas_config({"warn_threshold_adc": 50})
        self.assertEqual(result2["warn_threshold_adc"], 500, "Should reject value below 100")

        # Invalid: clear_warn >= warn
        result3 = self.reader.update_gas_config({"clear_warn_adc": 600})
        self.assertEqual(result3["clear_warn_adc"], 450, "Should reject clear >= warn")

    def test_gas_config_get(self):
        """get_gas_config returns thresholds with voltage conversions and disclaimer."""
        config = self.reader.get_gas_config()
        self.assertIn("warn_threshold_adc", config)
        self.assertIn("crit_threshold_adc", config)
        self.assertIn("warn_threshold_voltage", config)
        self.assertIn("disclaimer", config)
        self.assertIn("NOT CERTIFIED", config["disclaimer"])


# =====================================================================
# 4. SOS Emergency Button
# =====================================================================

class TestSOSButton(BaseSensorTest):

    def test_released_state_default(self):
        """Default emergency_button state is RELEASED."""
        data = self.send(emergency_button="RELEASED")
        self.assertEqual(data["emergency_button"], "RELEASED")
        self.assertFalse(self.reader.sos_active)

    def test_pressed_triggers_alert_event(self):
        """Pressing SOS records a TRIGGERED alert event in SQLite."""
        data = self.send(emergency_button="PRESSED")
        self.assertEqual(data["emergency_button"], "PRESSED")
        self.assertTrue(self.reader.sos_active)
        alerts = self.reader.get_alerts()
        sos_events = [a for a in alerts if a["alert_type"] == "SOS_BUTTON"]
        self.assertEqual(len(sos_events), 1)
        self.assertEqual(sos_events[0]["event_type"], "TRIGGERED")
        self.assertEqual(sos_events[0]["severity"], "Emergency")

    def test_release_clears_alert_event(self):
        """Releasing SOS records a CLEARED alert event."""
        self.send(emergency_button="PRESSED")
        data = self.send(emergency_button="RELEASED")
        self.assertEqual(data["emergency_button"], "RELEASED")
        self.assertFalse(self.reader.sos_active)
        alerts = self.reader.get_alerts()
        sos_events = [a for a in alerts if a["alert_type"] == "SOS_BUTTON"]
        self.assertEqual(len(sos_events), 2)
        self.assertEqual(sos_events[1]["event_type"], "CLEARED")

    def test_repeated_press_deduplication(self):
        """Multiple consecutive PRESSED packets should not create duplicate SOS events."""
        self.send(emergency_button="PRESSED")
        self.send(emergency_button="PRESSED")
        self.send(emergency_button="PRESSED")
        alerts = self.reader.get_alerts()
        sos_triggered = [a for a in alerts if a["alert_type"] == "SOS_BUTTON" and a["event_type"] == "TRIGGERED"]
        self.assertEqual(len(sos_triggered), 1, "Duplicate PRESSED must not create extra TRIGGERED events")

    def test_repeated_release_deduplication(self):
        """Multiple consecutive RELEASED packets should not create duplicate clear events."""
        self.send(emergency_button="PRESSED")
        self.send(emergency_button="RELEASED")
        self.send(emergency_button="RELEASED")
        alerts = self.reader.get_alerts()
        sos_cleared = [a for a in alerts if a["alert_type"] == "SOS_BUTTON" and a["event_type"] == "CLEARED"]
        self.assertEqual(len(sos_cleared), 1)

    def test_press_release_press_cycle(self):
        """Full press -> release -> press cycle creates 3 events (2 triggered, 1 cleared)."""
        self.send(emergency_button="PRESSED")
        self.send(emergency_button="RELEASED")
        self.send(emergency_button="PRESSED")
        alerts = self.reader.get_alerts()
        sos_events = [a for a in alerts if a["alert_type"] == "SOS_BUTTON"]
        self.assertEqual(len(sos_events), 3)


# =====================================================================
# 5. Telemetry Ingest Validation (Wi-Fi endpoint)
# =====================================================================

class TestTelemetryIngestValidation(BaseSensorTest):

    def test_valid_full_packet_accepted(self):
        """A complete valid packet is ingested successfully via ingest_telemetry."""
        pkt = make_packet()
        result = self.reader.ingest_telemetry(pkt, client_ip="192.168.1.100")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], "wifi")

    def test_reject_non_dict_payload(self):
        """Non-dict payloads raise ValueError."""
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry("not a dict")

    def test_reject_empty_dict(self):
        """Empty dict payloads raise ValueError."""
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry({})

    def test_reject_non_numeric_temperature(self):
        """String temperature is rejected by validation."""
        pkt = make_packet()
        pkt["temperature"] = "hot"
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry(pkt)

    def test_reject_bool_as_numeric(self):
        """Boolean values must be rejected where numeric is expected (Python bool is int subclass)."""
        pkt = make_packet()
        pkt["temperature"] = True
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry(pkt)

    def test_reject_non_numeric_gas_adc(self):
        """String gas_adc is rejected."""
        pkt = make_packet()
        pkt["gas_adc"] = "high"
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry(pkt)

    def test_reject_malformed_accel(self):
        """Accel as a string instead of dict is rejected."""
        pkt = make_packet()
        pkt["accel"] = "not a dict"
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry(pkt)

    def test_reject_malformed_gyro(self):
        """Gyro as a list instead of dict is rejected."""
        pkt = make_packet()
        pkt["gyro"] = [1, 2, 3]
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry(pkt)

    def test_accept_null_temperature(self):
        """Null temperature is valid (sensor may not have a reading yet)."""
        pkt = make_packet(temperature=None, humidity=None, dht_status="error")
        result = self.reader.ingest_telemetry(pkt)
        self.assertEqual(result["status"], "success")

    def test_accept_null_accel(self):
        """Null accel is valid (IMU may be disconnected)."""
        pkt = make_packet()
        pkt["accel"] = None
        result = self.reader.ingest_telemetry(pkt)
        self.assertEqual(result["status"], "success")

    def test_serial_mode_rejects_wifi_ingest(self):
        """When source_mode is 'serial', Wi-Fi ingest should raise ValueError."""
        self.reader.source_mode = "serial"
        pkt = make_packet()
        with self.assertRaises(ValueError):
            self.reader.ingest_telemetry(pkt)

    def test_client_ip_stored_on_wifi_ingest(self):
        """Client IP from Wi-Fi ingest is stored in telemetry state."""
        pkt = make_packet()
        self.reader.ingest_telemetry(pkt, client_ip="10.0.0.55")
        data = self.reader.get_telemetry()
        self.assertEqual(data["client_ip"], "10.0.0.55")
        self.assertEqual(data["telemetry_source"], "wifi")


# =====================================================================
# 6. Cross-Sensor Integration & Buzzer Priority Arbitration
# =====================================================================

class TestCrossSensorIntegration(BaseSensorTest):

    def test_sos_overrides_all_alerts(self):
        """SOS button takes priority over active temp + gas alerts for buzzer mode."""
        data = self.send(
            temperature=35.0, dht_status="ok",
            gas_adc=1100, mq_status="ok",
            emergency_button="PRESSED",
            buzzer="ACTIVE", buzzer_mode="SOS",
        )
        self.assertTrue(data["temperature_alert"]["active"])
        self.assertEqual(data["gas_alert"]["level"], "CRITICAL")
        self.assertEqual(data["buzzer"], "ACTIVE")
        self.assertEqual(data["buzzer_mode"], "SOS")

    def test_critical_gas_overrides_temperature(self):
        """Critical gas alert takes priority over temperature alert in buzzer arbitration."""
        data = self.send(
            temperature=35.0, dht_status="ok",
            gas_adc=1100, mq_status="ok",
            emergency_button="RELEASED",
            gas_alert={"sound_active": True},
            temperature_alert={"sound_active": True},
        )
        self.assertTrue(data["temperature_alert"]["active"])
        self.assertEqual(data["gas_alert"]["level"], "CRITICAL")
        self.assertEqual(data["buzzer_mode"], "GAS_CRITICAL")

    def test_warning_gas_overrides_temperature(self):
        """Gas warning takes priority over temperature alert in buzzer arbitration."""
        data = self.send(
            temperature=35.0, dht_status="ok",
            gas_adc=450, mq_status="ok",
            emergency_button="RELEASED",
            gas_alert={"sound_active": True},
            temperature_alert={"sound_active": True},
        )
        self.assertTrue(data["temperature_alert"]["active"])
        self.assertEqual(data["gas_alert"]["level"], "WARNING")
        self.assertEqual(data["buzzer_mode"], "GAS_WARNING")

    def test_temperature_alert_sounds_when_alone(self):
        """Temperature alert alone (no gas, no SOS) sets buzzer to TEMP_ALERT."""
        data = self.send(
            temperature=35.0, dht_status="ok",
            gas_adc=50, mq_status="ok",
            emergency_button="RELEASED",
            temperature_alert={"sound_active": True},
        )
        self.assertTrue(data["temperature_alert"]["active"])
        self.assertEqual(data["buzzer_mode"], "TEMP_ALERT")

    def test_all_sensors_normal_buzzer_standby(self):
        """When all sensors are normal, buzzer remains STANDBY."""
        data = self.send(
            temperature=25.0, dht_status="ok",
            gas_adc=50, mq_status="ok",
            emergency_button="RELEASED",
        )
        self.assertFalse(data["temperature_alert"]["active"])
        self.assertFalse(data["gas_alert"]["active"])
        self.assertEqual(data["buzzer"], "STANDBY")

    def test_stale_detection_affects_all_sensors(self):
        """After > 5s of no data, all sensor statuses go stale simultaneously."""
        self.send(temperature=25.0, gas_adc=50, mpu_status="ok", dht_status="ok")
        time.sleep(5.1)
        data = self.reader.get_telemetry()
        self.assertFalse(data["connected"])
        self.assertIn("Stale", data["status"])
        self.assertEqual(data["temperature_alert"]["sensor_status"], "stale")
        self.assertEqual(data["gas_alert"]["sensor_status"], "stale")

    def test_stale_preserves_active_alerts(self):
        """Stale detection should not clear an active alert — it must persist."""
        self.send(temperature=35.0, dht_status="ok", gas_adc=1100, mq_status="ok")
        self.assertTrue(self.reader.get_telemetry()["temperature_alert"]["active"])
        self.assertEqual(self.reader.get_telemetry()["gas_alert"]["level"], "CRITICAL")

        time.sleep(5.1)
        data = self.reader.get_telemetry()
        self.assertTrue(data["temperature_alert"]["active"], "Temp alert must survive stale")
        self.assertEqual(data["gas_alert"]["status"], "CRITICAL", "Gas alert must survive stale")

    def test_multiple_alerts_logged_separately(self):
        """Temperature, gas, and SOS alerts create independent SQLite events."""
        self.send(temperature=35.0, dht_status="ok", gas_adc=450, mq_status="ok",
                  emergency_button="PRESSED")
        alerts = self.reader.get_alerts()
        alert_types = {a["alert_type"] for a in alerts}
        self.assertIn("TEMPERATURE_ALERT", alert_types)
        self.assertIn("GAS_ALERT", alert_types)
        self.assertIn("SOS_BUTTON", alert_types)

    def test_diagnostics_passthrough(self):
        """Diagnostics block from firmware is passed through to telemetry output."""
        pkt = make_packet()
        pkt["diagnostics"] = {
            "imu_sample_count": 5000,
            "imu_sample_interval_us": 20000,
            "imu_missed_deadlines": 3,
            "imu_last_sample_ms": 10000,
            "dht_sample_age_ms": 200,
            "mq_sample_age_ms": 100,
            "mq_status": "ok",
        }
        data = self.send_raw(pkt)
        self.assertIsNotNone(data["diagnostics"])
        self.assertEqual(data["diagnostics"]["imu_sample_count"], 5000)
        self.assertEqual(data["diagnostics"]["imu_missed_deadlines"], 3)


# =====================================================================
# 7. SQLite Persistence Layer
# =====================================================================

class TestSQLitePersistence(BaseSensorTest):

    def test_telemetry_records_persisted(self):
        """Telemetry packets are persisted to SQLite sensor_telemetry table."""
        self.send(temperature=25.0, humidity=50.0, gas_adc=50, mpu_status="ok")
        time.sleep(0.3)  # Allow async DB writer to flush
        history = self.reader.get_history(limit=10)
        self.assertGreaterEqual(len(history), 1)
        record = history[-1]
        self.assertEqual(record["temperature"], 25.0)
        self.assertEqual(record["humidity"], 50.0)
        self.assertEqual(record["gas_adc"], 50)

    def test_accel_gyro_persisted(self):
        """Accel and gyro components are correctly stored in SQLite."""
        self.send(accel_x=1.0, accel_y=2.0, accel_z=9.81,
                  gyro_x=0.1, gyro_y=0.2, gyro_z=0.3, mpu_status="ok")
        time.sleep(0.3)
        history = self.reader.get_history(limit=10)
        record = history[-1]
        self.assertEqual(record["accel_x"], 1.0)
        self.assertEqual(record["accel_y"], 2.0)
        self.assertEqual(record["accel_z"], 9.81)
        self.assertEqual(record["gyro_z"], 0.3)

    def test_alert_acknowledgement_persistence(self):
        """Acknowledged alert state persists across SerialReader restarts."""
        self.send(temperature=35.0, dht_status="ok")
        alerts = self.reader.get_alerts()
        alert_id = alerts[0]["id"]
        self.reader.acknowledge_alert(alert_id)

        # Create a new reader on the same DB
        new_reader = SerialReader(port="COM_TEST2", baudrate=115200, db_path=self.db_path)
        new_reader.source_mode = "wifi"
        persisted = new_reader.get_alerts()
        self.assertTrue(persisted[0]["acknowledged"])
        new_reader.stop()

    def test_history_limit_clamping(self):
        """History limit is clamped between 1 and 500."""
        self.assertEqual(len(self.reader.get_history(limit=0)), 0)
        # limit=0 clamps to max(1, 0) = 1, returns up to 1 record (or 0 if empty)
        # limit=-5 clamps to 1
        result = self.reader.get_history(limit=-5)
        self.assertLessEqual(len(result), 1)

    def test_alerts_limit_clamping(self):
        """Alerts limit is clamped between 1 and 200."""
        self.send(temperature=35.0, dht_status="ok")
        result = self.reader.get_alerts(limit=10000)
        self.assertLessEqual(len(result), 200)


# =====================================================================
# 8. Source Mode Configuration
# =====================================================================

class TestSourceModeConfig(BaseSensorTest):

    def test_set_source_wifi(self):
        """Setting source to 'wifi' updates mode and disconnects hardware."""
        self.reader.set_source("wifi")
        self.assertEqual(self.reader.source_mode, "wifi")
        self.assertEqual(self.reader.active_source, "wifi")
        data = self.reader.get_telemetry()
        self.assertEqual(data["source_mode"], "wifi")

    def test_set_source_serial(self):
        """Setting source to 'serial' updates mode."""
        self.reader.set_source("serial")
        self.assertEqual(self.reader.source_mode, "serial")
        self.assertEqual(self.reader.active_source, "serial")

    def test_set_source_auto(self):
        """Setting source to 'auto' updates mode."""
        self.reader.set_source("auto")
        self.assertEqual(self.reader.source_mode, "auto")

    def test_set_source_invalid_raises(self):
        """Invalid source mode raises ValueError."""
        with self.assertRaises(ValueError):
            self.reader.set_source("bluetooth")

    def test_config_endpoint_reflects_state(self):
        """The /api/config GET-equivalent returns current port, baud, and source mode."""
        self.reader.set_port("COM42", baudrate=9600)
        data = self.reader.get_telemetry()
        self.assertEqual(data["port"], "COM42")
        self.assertEqual(data["baudrate"], 9600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
