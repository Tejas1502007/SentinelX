"""
Unit & Software Validation Tests for SENTINEL-X Motion Analysis Module
Distinguishes software validation tests from physical hardware validation.
Uses real recorded stationary and active samples from sentinel_x.db.
"""

import math
import sqlite3
import time
import os
import unittest
from motion_analyzer import MotionAnalyzer

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sentinel_x.db")


class TestMotionAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = MotionAnalyzer(
            intensity_low_max=0.50,
            intensity_mod_max=2.50,
            impact_accel_peak=24.5,
            impact_cooldown_s=3.0,
            gravity_tolerance=1.50,
            gyro_max_rads=0.50,
            inactivity_std_max=0.35,
            inactivity_duration_s=5.0,  # 5 seconds for rapid testing
            min_valid_samples=40,
            freshness_timeout_s=2.0
        )

    def test_stationary_real_database_records(self):
        """
        Verify that real stationary readings from sentinel_x.db
        produce LOW movement intensity, GOOD quality, and valid static tilt estimate.
        """
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # Find 10 consecutive records where accel_x, accel_y, accel_z are near static gravity
        cursor.execute("""
            SELECT timestamp, accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z
            FROM sensor_telemetry
            WHERE mpu_status = 'ok' AND accel_x IS NOT NULL
            ORDER BY id ASC
            LIMIT 20
        """)
        rows = cursor.fetchall()
        conn.close()

        self.assertTrue(len(rows) > 0, "Database should contain recorded telemetry rows")

        base_time = 1000.0
        for i, row in enumerate(rows):
            ts, ax, ay, az, gx, gy, gz = row
            motion_features = {
                "accel_magnitude_mps2": math.sqrt(ax*ax + ay*ay + az*az),
                "accel_magnitude_g": math.sqrt(ax*ax + ay*ay + az*az) / 9.80665,
                "gyro_magnitude_rads": math.sqrt(gx*gx + gy*gy + gz*gz),
                "window_samples": 50,
                "window_duration_ms": 1000,
                "accel_mag_mean_mps2": math.sqrt(ax*ax + ay*ay + az*az),
                "accel_mag_peak_mps2": math.sqrt(ax*ax + ay*ay + az*az) + 0.05,
                "accel_mag_std_mps2": 0.08,  # Typical stationary noise
                "gyro_mag_mean_rads": 0.02,
                "gyro_mag_peak_rads": 0.05,
                "window_complete": True
            }
            accel = {"x": ax, "y": ay, "z": az}
            gyro = {"x": gx, "y": gy, "z": gz}

            analysis, event = self.analyzer.process(
                current_time=base_time + (i * 0.5),
                mpu_status="ok",
                accel=accel,
                gyro=gyro,
                motion_features=motion_features
            )

            # Assertions for stationary behavior
            self.assertEqual(analysis["data_quality"]["status"], "GOOD")
            self.assertEqual(analysis["movement_intensity"]["level"], "LOW")
            self.assertEqual(analysis["orientation_estimate"]["status"], "STABLE_ESTIMATE")
            self.assertFalse(analysis["orientation_estimate"]["dynamic_gated"])
            self.assertIsNotNone(analysis["orientation_estimate"]["pitch_deg"])
            self.assertIsNotNone(analysis["orientation_estimate"]["roll_deg"])
            self.assertIsNotNone(analysis["orientation_estimate"]["tilt_from_vertical_deg"])

    def test_dynamic_acceleration_gate_locks_tilt(self):
        """
        Verify that dynamic acceleration (movement / shaking) activates the gate
        and locks/holds the last stable orientation rather than outputting corrupt tilt.
        """
        t = 1000.0
        # 1. Establish stable baseline
        accel_stable = {"x": 0.0, "y": 0.0, "z": 9.81}
        gyro_stable = {"x": 0.0, "y": 0.0, "z": 0.0}
        mf_stable = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 0.05, "accel_mag_peak_mps2": 9.85, "gyro_mag_peak_rads": 0.02
        }
        res_stable, _ = self.analyzer.process(t, "ok", accel_stable, gyro_stable, mf_stable)
        self.assertEqual(res_stable["orientation_estimate"]["status"], "STABLE_ESTIMATE")
        self.assertAlmostEqual(res_stable["orientation_estimate"]["tilt_from_vertical_deg"], 0.0, places=0)

        # 2. Introduce high dynamic acceleration (e.g. 16.0 m/s^2 linear motion + 2.0 rad/s spin)
        t += 0.5
        accel_dynamic = {"x": 12.0, "y": 8.0, "z": 10.0}  # |a| = 17.5 m/s^2 >> 1g
        gyro_dynamic = {"x": 1.5, "y": 2.0, "z": 0.8}    # |gyro| = 2.6 rad/s >> 0.5
        mf_dynamic = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 3.2, "accel_mag_peak_mps2": 17.5, "gyro_mag_peak_rads": 2.6
        }
        res_dynamic, _ = self.analyzer.process(t, "ok", accel_dynamic, gyro_dynamic, mf_dynamic)

        # Assert gate is active and status is DYNAMIC_LOCKED
        self.assertTrue(res_dynamic["orientation_estimate"]["dynamic_gated"])
        self.assertEqual(res_dynamic["orientation_estimate"]["status"], "DYNAMIC_LOCKED")
        self.assertEqual(res_dynamic["movement_intensity"]["level"], "HIGH")

    def test_impact_event_cooldown_and_deduplication(self):
        """
        Verify impact detection triggers on peak >= 24.5 m/s^2,
        generates an event, and suppresses duplicate events during cooldown.
        """
        t = 2000.0
        accel_normal = {"x": 0.0, "y": 0.0, "z": 9.81}
        gyro_normal = {"x": 0.0, "y": 0.0, "z": 0.0}
        mf_normal = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 0.1, "accel_mag_peak_mps2": 9.9
        }

        # Baseline
        self.analyzer.process(t, "ok", accel_normal, gyro_normal, mf_normal)

        # 1. Sudden impact peak of 28.0 m/s^2 (~2.85 g)
        t += 0.5
        mf_impact = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 2.5, "accel_mag_peak_mps2": 28.0
        }
        res_impact, event_impact = self.analyzer.process(t, "ok", accel_normal, gyro_normal, mf_impact)

        self.assertTrue(res_impact["impact_event"]["detected_now"])
        self.assertIsNotNone(event_impact)
        self.assertEqual(event_impact["alert_type"], "IMPACT_LIKE_EVENT")
        self.assertEqual(event_impact["event_type"], "TRIGGERED")
        self.assertEqual(event_impact["severity"], "Warning")
        self.assertIn("not a confirmed fall", event_impact["message"])

        # 2. Secondary bounce/echo within 1.0 second (during 3.0s cooldown)
        t += 1.0
        mf_bounce = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 2.0, "accel_mag_peak_mps2": 26.5
        }
        res_bounce, event_bounce = self.analyzer.process(t, "ok", accel_normal, gyro_normal, mf_bounce)

        # Cooldown must suppress duplicate event
        self.assertFalse(res_bounce["impact_event"]["detected_now"])
        self.assertTrue(res_bounce["impact_event"]["cooldown_active"])
        self.assertIsNone(event_bounce, "Duplicate impact must be suppressed during cooldown")

        # 3. Advance through cooldown at regular 0.5s intervals (so stream remains fresh)
        for _ in range(6):  # 3.0s total elapsed
            t += 0.5
            self.analyzer.process(t, "ok", accel_normal, gyro_normal, mf_normal)

        # After cooldown elapsed, a new impact CAN trigger
        t += 0.5
        mf_impact2 = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 2.5, "accel_mag_peak_mps2": 25.5
        }
        res_impact2, event_impact2 = self.analyzer.process(t, "ok", accel_normal, gyro_normal, mf_impact2)
        self.assertTrue(res_impact2["impact_event"]["detected_now"])
        self.assertIsNotNone(event_impact2)

    def test_prolonged_low_movement_observation(self):
        """
        Verify continuous low movement triggers PROLONGED_LOW_MOVEMENT after 5.0 seconds,
        deduplicates repeated notices, and clears when movement resumes.
        """
        t = 3000.0
        accel = {"x": 0.0, "y": 0.0, "z": 9.81}
        gyro = {"x": 0.0, "y": 0.0, "z": 0.0}
        mf_quiet = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 0.10, "accel_mag_peak_mps2": 9.85
        }

        # 1. 0s to 4.5s: quiet monitoring
        for step in range(9):
            res, evt = self.analyzer.process(t + (step * 0.5), "ok", accel, gyro, mf_quiet)
            self.assertEqual(res["inactivity_observation"]["status"], "LOW_MOVEMENT_MONITORING")
            self.assertFalse(res["inactivity_observation"]["is_prolonged"])
            self.assertIsNone(evt)

        # 2. At 5.5s (threshold = 5.0s): trigger PROLONGED_LOW_MOVEMENT
        t_trigger = t + 5.5
        res_trig, evt_trig = self.analyzer.process(t_trigger, "ok", accel, gyro, mf_quiet)
        self.assertTrue(res_trig["inactivity_observation"]["is_prolonged"])
        self.assertEqual(res_trig["inactivity_observation"]["status"], "PROLONGED_LOW_MOVEMENT")
        self.assertIsNotNone(evt_trig)
        self.assertEqual(evt_trig["alert_type"], "PROLONGED_LOW_MOVEMENT")
        self.assertEqual(evt_trig["event_type"], "TRIGGERED")
        self.assertIn("not confirmed worker-down", evt_trig["message"])

        # 3. Subsequent quiet samples while in prolonged state should NOT spam events (deduplication)
        res_next, evt_next = self.analyzer.process(t_trigger + 0.5, "ok", accel, gyro, mf_quiet)
        self.assertTrue(res_next["inactivity_observation"]["is_prolonged"])
        self.assertIsNone(evt_next, "Must not fire duplicate event while staying in prolonged inactivity")

        # 4. Movement resumes (std dev = 1.2 m/s^2)
        mf_active = {
            "window_samples": 50, "window_duration_ms": 1000, "window_complete": True,
            "accel_mag_std_mps2": 1.20, "accel_mag_peak_mps2": 11.5
        }
        res_clear, evt_clear = self.analyzer.process(t_trigger + 1.0, "ok", accel, gyro, mf_active)
        self.assertFalse(res_clear["inactivity_observation"]["is_prolonged"])
        self.assertEqual(res_clear["inactivity_observation"]["status"], "ACTIVE")
        self.assertIsNotNone(evt_clear)
        self.assertEqual(evt_clear["event_type"], "CLEARED")

    def test_invalid_and_stale_sensor_handling(self):
        """
        Verify sensor error/stale reports UNKNOWN / INSUFFICIENT_DATA and drops quality.
        """
        t = 4000.0
        # MPU Error
        res_err, _ = self.analyzer.process(t, "error", None, None, None)
        self.assertEqual(res_err["data_quality"]["status"], "FAULT")
        self.assertEqual(res_err["movement_intensity"]["level"], "UNKNOWN")
        self.assertEqual(res_err["orientation_estimate"]["status"], "UNKNOWN")

        # Stale timeout
        res_stale, _ = self.analyzer.process(t + 10.0, "ok", {"x": 0.0, "y": 0.0, "z": 9.81}, None, None)
        self.assertEqual(res_stale["data_quality"]["status"], "STALE")


if __name__ == "__main__":
    unittest.main()
