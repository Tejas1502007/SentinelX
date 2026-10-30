"""
SENTINEL-X — Experimental Rule-Based MPU6050 Motion Analysis Module
Zero-dependency, transparent rule-based signal evaluation engine.
Does NOT use machine learning.

Derives:
1. Movement Intensity (acceleration-magnitude variance over 1-second rolling window)
2. Impact-like Events (peak threshold with cooldown and deduplication)
3. Orientation / Tilt Estimate (gated by dynamic acceleration and rotation)
4. Prolonged Low-Movement Observation (continuous low-intensity timer with deduplication)
5. Motion Data Quality (window completeness, sample count, gaps, and freshness)

Safety Boundary:
- All indicators are EXPERIMENTAL.
- Explicitly NOT certified as confirmed falls, man-down, or posture detection.
- Raw sensor values are isolated and preserved without modification.
"""

import math
import time
from typing import Dict, Any, Optional, Tuple


class MotionAnalyzer:
    """
    Stateful rule-based motion analysis engine.
    Processes 50 Hz window statistics and instantaneous tri-axial IMU data.
    """

    def __init__(
        self,
        intensity_low_max: float = 0.50,        # m/s^2 (~0.05 g)
        intensity_mod_max: float = 2.50,        # m/s^2 (~0.25 g)
        impact_accel_peak: float = 24.5,        # m/s^2 (~2.50 g)
        impact_cooldown_s: float = 3.0,         # Cooldown between impacts
        gravity_tolerance: float = 1.50,        # m/s^2 tolerance around 1g (9.81 m/s^2)
        gyro_max_rads: float = 0.50,            # rad/s (~28.6 deg/s) max rotation for tilt
        inactivity_std_max: float = 0.35,       # m/s^2 threshold for low movement
        inactivity_duration_s: float = 15.0,    # Configurable seconds before prolonged observation
        min_valid_samples: int = 40,            # Min samples in 1s window (out of 50)
        freshness_timeout_s: float = 1.5,       # Stale data timeout
    ):
        # Configurable experimental thresholds
        self.INTENSITY_LOW_MAX = float(intensity_low_max)
        self.INTENSITY_MOD_MAX = float(intensity_mod_max)
        self.IMPACT_ACCEL_PEAK = float(impact_accel_peak)
        self.IMPACT_COOLDOWN_S = float(impact_cooldown_s)
        self.GRAVITY_TOLERANCE = float(gravity_tolerance)
        self.GYRO_MAX_RADS = float(gyro_max_rads)
        self.INACTIVITY_STD_MAX = float(inactivity_std_max)
        self.INACTIVITY_DURATION_S = float(inactivity_duration_s)
        self.MIN_VALID_SAMPLES = int(min_valid_samples)
        self.FRESHNESS_TIMEOUT_S = float(freshness_timeout_s)

        # Standard physical constants
        self.G_MPS2 = 9.80665

        # Internal state tracking
        self.last_sample_time: Optional[float] = None
        self.last_impact_time: float = 0.0
        self.recent_impacts_count: int = 0
        self.last_impact_event: Optional[Dict[str, Any]] = None

        # Low-movement observation state
        self.low_movement_start_time: Optional[float] = None
        self.low_movement_elapsed_s: float = 0.0
        self.prolonged_inactivity_active: bool = False
        self.prolonged_inactivity_event_logged: bool = False

        # Last stable orientation estimate (held when dynamic motion occurs)
        self.last_stable_pitch_deg: Optional[float] = None
        self.last_stable_roll_deg: Optional[float] = None
        self.last_stable_tilt_deg: Optional[float] = None
        self.last_stable_orientation_time: Optional[float] = None

    def get_config(self) -> Dict[str, Any]:
        """Return the current configurable experimental thresholds and units."""
        return {
            "intensity_low_max_mps2": self.INTENSITY_LOW_MAX,
            "intensity_mod_max_mps2": self.INTENSITY_MOD_MAX,
            "impact_accel_peak_mps2": self.IMPACT_ACCEL_PEAK,
            "impact_accel_peak_g": round(self.IMPACT_ACCEL_PEAK / self.G_MPS2, 2),
            "impact_cooldown_s": self.IMPACT_COOLDOWN_S,
            "gravity_tolerance_mps2": self.GRAVITY_TOLERANCE,
            "gyro_max_rads": self.GYRO_MAX_RADS,
            "inactivity_std_max_mps2": self.INACTIVITY_STD_MAX,
            "inactivity_duration_s": self.INACTIVITY_DURATION_S,
            "min_valid_samples": self.MIN_VALID_SAMPLES,
            "freshness_timeout_s": self.FRESHNESS_TIMEOUT_S,
        }

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Safely update configurable thresholds with validation."""
        if "inactivity_duration_s" in updates:
            val = float(updates["inactivity_duration_s"])
            if 3.0 <= val <= 300.0:
                self.INACTIVITY_DURATION_S = val
        if "impact_accel_peak_mps2" in updates:
            val = float(updates["impact_accel_peak_mps2"])
            if 15.0 <= val <= 50.0:
                self.IMPACT_ACCEL_PEAK = val
        if "impact_cooldown_s" in updates:
            val = float(updates["impact_cooldown_s"])
            if 0.5 <= val <= 30.0:
                self.IMPACT_COOLDOWN_S = val
        if "intensity_low_max_mps2" in updates:
            val = float(updates["intensity_low_max_mps2"])
            if 0.1 <= val <= 2.0:
                self.INTENSITY_LOW_MAX = val
        if "intensity_mod_max_mps2" in updates:
            val = float(updates["intensity_mod_max_mps2"])
            if 1.0 <= val <= 10.0:
                self.INTENSITY_MOD_MAX = val
        return self.get_config()

    def process(
        self,
        current_time: float,
        mpu_status: str,
        accel: Optional[Dict[str, Optional[float]]],
        gyro: Optional[Dict[str, Optional[float]]],
        motion_features: Optional[Dict[str, Any]],
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Evaluate rule-based motion analysis.
        Returns:
            Tuple of (analysis_payload: Dict, new_alert_event: Optional[Dict])
        """
        now = float(current_time) if current_time else time.time()
        new_event = None

        # -----------------------------------------------------------------
        # 1. Freshness & Data Quality Validation
        # -----------------------------------------------------------------
        is_fresh = False
        sample_age_s = None
        if self.last_sample_time is not None:
            sample_age_s = max(0.0, now - self.last_sample_time)
            is_fresh = sample_age_s < self.FRESHNESS_TIMEOUT_S
        else:
            is_fresh = True
        self.last_sample_time = now

        mpu_ok = (mpu_status == "ok")

        # Extract window features if present
        mf = motion_features or {}
        valid_samples = mf.get("window_samples", 0) or 0
        window_duration_ms = mf.get("window_duration_ms", 0) or 0
        window_complete = bool(mf.get("window_complete", False))
        accel_mag_std = mf.get("accel_mag_std_mps2")
        accel_mag_peak = mf.get("accel_mag_peak_mps2")
        accel_mag_mean = mf.get("accel_mag_mean_mps2")
        gyro_mag_peak = mf.get("gyro_mag_peak_rads")
        gyro_mag_mean = mf.get("gyro_mag_mean_rads")

        # Extract instantaneous vector components
        ax = accel.get("x") if isinstance(accel, dict) else None
        ay = accel.get("y") if isinstance(accel, dict) else None
        az = accel.get("z") if isinstance(accel, dict) else None

        gx = gyro.get("x") if isinstance(gyro, dict) else None
        gy = gyro.get("y") if isinstance(gyro, dict) else None
        gz = gyro.get("z") if isinstance(gyro, dict) else None

        has_valid_vectors = (
            ax is not None and ay is not None and az is not None and
            not math.isnan(ax) and not math.isnan(ay) and not math.isnan(az)
        )

        # Fallback instantaneous magnitudes if window mag is missing
        inst_accel_mag = None
        if has_valid_vectors:
            inst_accel_mag = math.sqrt(ax * ax + ay * ay + az * az)

        inst_gyro_mag = None
        if (
            gx is not None and gy is not None and gz is not None and
            not math.isnan(gx) and not math.isnan(gy) and not math.isnan(gz)
        ):
            inst_gyro_mag = math.sqrt(gx * gx + gy * gy + gz * gz)

        # Determine motion data quality
        quality_score = 0
        quality_status = "UNKNOWN"
        quality_reasons = []

        if not mpu_ok:
            quality_status = "FAULT" if mpu_status in ("error", "disconnected") else "STALE"
            quality_reasons.append(f"MPU status: {mpu_status}")
        elif not is_fresh:
            quality_status = "STALE"
            quality_reasons.append("Telemetry reception interval exceeded freshness threshold")
        elif not has_valid_vectors:
            quality_status = "INSUFFICIENT_DATA"
            quality_reasons.append("Missing or non-finite acceleration components")
        else:
            # Score components (0-100)
            score = 40  # Base for valid live stream
            if valid_samples >= self.MIN_VALID_SAMPLES:
                score += 30
            elif valid_samples > 20:
                score += 15
                quality_reasons.append(f"Reduced sample density ({valid_samples}/50 in window)")
            else:
                quality_reasons.append(f"Insufficient window samples ({valid_samples}/50)")

            if 800 <= window_duration_ms <= 1200:
                score += 20
            elif window_duration_ms > 0:
                score += 10

            if window_complete:
                score += 10

            quality_score = min(100, score)

            if quality_score >= 80 and valid_samples >= self.MIN_VALID_SAMPLES:
                quality_status = "GOOD"
            elif quality_score >= 50:
                quality_status = "DEGRADED"
            else:
                quality_status = "INSUFFICIENT_DATA"

        is_data_reliable = (quality_status in ("GOOD", "DEGRADED"))

        # -----------------------------------------------------------------
        # 2. Movement Intensity (Variation over Rolling Window)
        # -----------------------------------------------------------------
        intensity_mps2: Optional[float] = None
        intensity_level = "UNKNOWN"
        intensity_rationale = ""

        if not is_data_reliable:
            intensity_level = "INSUFFICIENT_DATA" if quality_status == "INSUFFICIENT_DATA" else "UNKNOWN"
            intensity_rationale = "Sensor data unreliable or unavailable"
        else:
            if accel_mag_std is not None and not math.isnan(accel_mag_std):
                intensity_mps2 = round(float(accel_mag_std), 3)
            else:
                # If window std is not pre-computed, use deviation from 1g baseline as fallback proxy
                if inst_accel_mag is not None:
                    intensity_mps2 = round(abs(inst_accel_mag - self.G_MPS2), 3)

            if intensity_mps2 is not None:
                if intensity_mps2 < self.INTENSITY_LOW_MAX:
                    intensity_level = "LOW"
                    intensity_rationale = f"Low movement variation (<{self.INTENSITY_LOW_MAX} m/s²); resting/stationary"
                elif intensity_mps2 < self.INTENSITY_MOD_MAX:
                    intensity_level = "MODERATE"
                    intensity_rationale = f"Moderate dynamic acceleration ({self.INTENSITY_LOW_MAX}–{self.INTENSITY_MOD_MAX} m/s²); routine activity"
                else:
                    intensity_level = "HIGH"
                    intensity_rationale = f"High dynamic acceleration (>{self.INTENSITY_MOD_MAX} m/s²); vigorous movement/vibration"

        # -----------------------------------------------------------------
        # 3. Orientation & Tilt Estimate (Dynamic Acceleration Gate)
        # -----------------------------------------------------------------
        pitch_deg: Optional[float] = None
        roll_deg: Optional[float] = None
        tilt_deg: Optional[float] = None
        orientation_status = "UNKNOWN"
        orientation_gated = False

        if is_data_reliable and has_valid_vectors:
            current_mag = inst_accel_mag if inst_accel_mag is not None else (accel_mag_mean or self.G_MPS2)
            mag_diff = abs(current_mag - self.G_MPS2)
            gyro_mag = (gyro_mag_peak or inst_gyro_mag or 0.0)
            std_val = intensity_mps2 if intensity_mps2 is not None else 0.0

            # Dynamic gate: only calculate tilt when static conditions prevail
            # (near 1g magnitude, low angular velocity, low window variation)
            is_static = (
                mag_diff <= self.GRAVITY_TOLERANCE and
                gyro_mag <= self.GYRO_MAX_RADS and
                std_val <= (self.INTENSITY_LOW_MAX * 1.6)
            )

            if is_static:
                # Pitch: rotation around Y-axis (tilt forward/backward)
                pitch_rad = math.atan2(ax, math.sqrt(ay * ay + az * az))
                pitch_deg = round(math.degrees(pitch_rad), 1)

                # Roll: rotation around X-axis (tilt left/right)
                roll_rad = math.atan2(ay, math.sqrt(ax * ax + az * az))
                roll_deg = round(math.degrees(roll_rad), 1)

                # Tilt angle from vertical Z-axis [0 deg = perfectly upright, 90 deg = horizontal]
                denom = max(current_mag, 0.001)
                cos_tilt = max(-1.0, min(1.0, az / denom))
                tilt_rad = math.acos(abs(cos_tilt))  # Angle relative to vertical
                tilt_deg = round(math.degrees(tilt_rad), 1)

                # Update stable memory
                self.last_stable_pitch_deg = pitch_deg
                self.last_stable_roll_deg = roll_deg
                self.last_stable_tilt_deg = tilt_deg
                self.last_stable_orientation_time = now

                orientation_status = "STABLE_ESTIMATE"
                orientation_gated = False
            else:
                # Dynamic motion corrupts accelerometer tilt! Gate activated.
                orientation_gated = True
                if self.last_stable_pitch_deg is not None:
                    pitch_deg = self.last_stable_pitch_deg
                    roll_deg = self.last_stable_roll_deg
                    tilt_deg = self.last_stable_tilt_deg
                    orientation_status = "DYNAMIC_LOCKED"
                else:
                    orientation_status = "DYNAMIC_UNSTABLE"

        # -----------------------------------------------------------------
        # 4. Impact-Like Event Detection (Peak, Cooldown, Deduplication)
        # -----------------------------------------------------------------
        impact_detected_now = False
        effective_peak = accel_mag_peak if accel_mag_peak is not None else inst_accel_mag

        if is_data_reliable and effective_peak is not None:
            if effective_peak >= self.IMPACT_ACCEL_PEAK:
                cooldown_elapsed = now - self.last_impact_time
                if cooldown_elapsed >= self.IMPACT_COOLDOWN_S:
                    impact_detected_now = True
                    self.last_impact_time = now
                    self.recent_impacts_count += 1

                    peak_g = round(effective_peak / self.G_MPS2, 2)
                    self.last_impact_event = {
                        "timestamp": now,
                        "peak_mps2": round(effective_peak, 2),
                        "peak_g": peak_g,
                        "cooldown_s": self.IMPACT_COOLDOWN_S,
                        "status": "DETECTED"
                    }

                    # Deduplicated event ready for SQLite persistence
                    new_event = {
                        "alert_type": "IMPACT_LIKE_EVENT",
                        "event_type": "TRIGGERED",
                        "severity": "Warning",
                        "message": (
                            f"Experimental impact-like acceleration peak: {effective_peak:.1f} m/s² "
                            f"({peak_g:.1f} g, threshold >={self.IMPACT_ACCEL_PEAK:.1f} m/s²). "
                            "Notice: Rule-based indicator, not a confirmed fall."
                        ),
                        "sensor_value": round(effective_peak, 2),
                        "threshold_value": self.IMPACT_ACCEL_PEAK
                    }

        cooldown_remaining_s = max(0.0, self.IMPACT_COOLDOWN_S - (now - self.last_impact_time))

        # -----------------------------------------------------------------
        # 5. Prolonged Low-Movement Observation
        # -----------------------------------------------------------------
        if is_data_reliable and intensity_mps2 is not None:
            is_low_movement = (intensity_mps2 <= self.INACTIVITY_STD_MAX)

            if is_low_movement:
                if self.low_movement_start_time is None:
                    self.low_movement_start_time = now
                    self.low_movement_elapsed_s = 0.0
                else:
                    self.low_movement_elapsed_s = max(0.0, now - self.low_movement_start_time)

                if self.low_movement_elapsed_s >= self.INACTIVITY_DURATION_S:
                    self.prolonged_inactivity_active = True
                    if not self.prolonged_inactivity_event_logged:
                        self.prolonged_inactivity_event_logged = True
                        new_event = {
                            "alert_type": "PROLONGED_LOW_MOVEMENT",
                            "event_type": "TRIGGERED",
                            "severity": "Warning",
                            "message": (
                                f"Prolonged low-movement observation: {self.low_movement_elapsed_s:.1f}s "
                                f"with variation <={self.INACTIVITY_STD_MAX} m/s² "
                                f"(threshold >={self.INACTIVITY_DURATION_S:.0f}s). "
                                "Notice: Observation indicator, not confirmed worker-down."
                            ),
                            "sensor_value": round(self.low_movement_elapsed_s, 1),
                            "threshold_value": self.INACTIVITY_DURATION_S
                        }
            else:
                # Active movement detected: reset inactivity timer
                if self.prolonged_inactivity_active and self.prolonged_inactivity_event_logged:
                    # Log clear event
                    new_event = {
                        "alert_type": "PROLONGED_LOW_MOVEMENT",
                        "event_type": "CLEARED",
                        "severity": "Info",
                        "message": (
                            f"Movement resumed ({intensity_mps2:.2f} m/s²). "
                            "Prolonged low-movement observation cleared."
                        ),
                        "sensor_value": round(intensity_mps2, 2),
                        "threshold_value": self.INACTIVITY_STD_MAX
                    }
                self.low_movement_start_time = None
                self.low_movement_elapsed_s = 0.0
                self.prolonged_inactivity_active = False
                self.prolonged_inactivity_event_logged = False
        else:
            # Data invalid or unavailable: do not advance inactivity timer
            self.low_movement_start_time = None
            self.low_movement_elapsed_s = 0.0
            self.prolonged_inactivity_active = False
            self.prolonged_inactivity_event_logged = False

        inactivity_state = (
            "PROLONGED_LOW_MOVEMENT" if self.prolonged_inactivity_active
            else "LOW_MOVEMENT_MONITORING" if (self.low_movement_start_time is not None)
            else "ACTIVE" if is_data_reliable
            else "UNKNOWN"
        )

        # -----------------------------------------------------------------
        # Assembly of Analysis Output Payload
        # -----------------------------------------------------------------
        analysis_payload = {
            "evaluation_time": now,
            "safety_disclaimer": "EXPERIMENTAL RULE-BASED INDICATORS ONLY. NOT CERTIFIED LIFE-SAFETY ALARM.",
            "data_quality": {
                "status": quality_status,
                "score_pct": quality_score,
                "valid_samples": valid_samples,
                "window_duration_ms": window_duration_ms,
                "window_complete": window_complete,
                "is_fresh": is_fresh,
                "reasons": quality_reasons
            },
            "movement_intensity": {
                "level": intensity_level,
                "value_mps2": intensity_mps2,
                "low_threshold": self.INTENSITY_LOW_MAX,
                "moderate_threshold": self.INTENSITY_MOD_MAX,
                "rationale": intensity_rationale
            },
            "orientation_estimate": {
                "status": orientation_status,
                "dynamic_gated": orientation_gated,
                "pitch_deg": pitch_deg,
                "roll_deg": roll_deg,
                "tilt_from_vertical_deg": tilt_deg,
                "last_stable_time": self.last_stable_orientation_time
            },
            "impact_event": {
                "detected_now": impact_detected_now,
                "cooldown_active": cooldown_remaining_s > 0,
                "cooldown_remaining_s": round(cooldown_remaining_s, 1),
                "threshold_peak_mps2": self.IMPACT_ACCEL_PEAK,
                "threshold_peak_g": round(self.IMPACT_ACCEL_PEAK / self.G_MPS2, 2),
                "last_impact": self.last_impact_event,
                "recent_count": self.recent_impacts_count
            },
            "inactivity_observation": {
                "status": inactivity_state,
                "elapsed_seconds": round(self.low_movement_elapsed_s, 1),
                "threshold_seconds": self.INACTIVITY_DURATION_S,
                "intensity_std_max": self.INACTIVITY_STD_MAX,
                "is_prolonged": self.prolonged_inactivity_active
            }
        }

        return analysis_payload, new_event
