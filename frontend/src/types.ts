export type Vector3 = { x: number | null; y: number | null; z: number | null };

export type MotionFeatures = {
  accel_magnitude_mps2: number | null;
  accel_magnitude_g: number | null;
  gyro_magnitude_rads: number | null;
  window_samples: number;
  window_duration_ms: number;
  accel_mag_mean_mps2: number | null;
  accel_mag_peak_mps2: number | null;
  accel_mag_std_mps2: number | null;
  gyro_mag_mean_rads: number | null;
  gyro_mag_peak_rads: number | null;
  window_complete: boolean;
};

export type DeviceDiagnostics = {
  imu_sample_count: number;
  imu_sample_interval_us: number;
  imu_missed_deadlines: number;
  imu_last_sample_ms: number;
  dht_sample_age_ms?: number;
  mq_sample_age_ms?: number;
  mq_status?: string;
};

export type MotionDataQuality = {
  status: "GOOD" | "DEGRADED" | "INSUFFICIENT_DATA" | "FAULT" | "STALE" | "UNKNOWN" | string;
  score_pct: number;
  valid_samples: number;
  window_duration_ms: number;
  window_complete: boolean;
  is_fresh: boolean;
  reasons: string[];
};

export type MovementIntensity = {
  level: "LOW" | "MODERATE" | "HIGH" | "INSUFFICIENT_DATA" | "UNKNOWN" | string;
  value_mps2: number | null;
  low_threshold: number;
  moderate_threshold: number;
  rationale: string;
};

export type OrientationEstimate = {
  status: "STABLE_ESTIMATE" | "DYNAMIC_LOCKED" | "DYNAMIC_UNSTABLE" | "UNKNOWN" | string;
  dynamic_gated: boolean;
  pitch_deg: number | null;
  roll_deg: number | null;
  tilt_from_vertical_deg: number | null;
  last_stable_time: number | null;
};

export type ImpactEventInfo = {
  detected_now: boolean;
  cooldown_active: boolean;
  cooldown_remaining_s: number;
  threshold_peak_mps2: number;
  threshold_peak_g: number;
  last_impact: {
    timestamp: number;
    peak_mps2: number;
    peak_g: number;
    cooldown_s: number;
    status: string;
  } | null;
  recent_count: number;
};

export type InactivityObservation = {
  status: "ACTIVE" | "LOW_MOVEMENT_MONITORING" | "PROLONGED_LOW_MOVEMENT" | "UNKNOWN" | string;
  elapsed_seconds: number;
  threshold_seconds: number;
  intensity_std_max: number;
  is_prolonged: boolean;
};

export type MotionAnalysis = {
  evaluation_time: number;
  safety_disclaimer: string;
  data_quality: MotionDataQuality;
  movement_intensity: MovementIntensity;
  orientation_estimate: OrientationEstimate;
  impact_event: ImpactEventInfo;
  inactivity_observation: InactivityObservation;
};

export type Telemetry = {
  connected: boolean;
  status: string;
  port: string;
  baudrate: number;
  last_update: number | null;
  uptime_ms: number | null;
  dht_status: string;
  mpu_status: string;
  mq_status?: string;
  temperature: number | null;
  humidity: number | null;
  temperature_alert?: TemperatureAlert;
  gas_alert?: GasAlert;
  accel: Vector3 | null;
  gyro: Vector3 | null;
  gas_adc: number | null;
  gas_voltage_est: number | null;
  gas_response?: GasResponse;
  emergency_button: string;
  buzzer?: string;
  motion?: MotionFeatures | null;
  motion_analysis?: MotionAnalysis | null;
  diagnostics?: DeviceDiagnostics | null;
  raw_log?: string[];
  telemetry_source?: string;
  source_mode?: string;
  buzzer_mode?: string;
  client_ip?: string | null;
};

export type GasResponse = {
  sensor_voltage_v: number | null;
  baseline_adc: number | null;
  baseline_captured_at: number | null;
  baseline_operator_identity: string | null;
  relative_change_percent: number | null;
  trend: "RISING" | "STABLE" | "FALLING" | string;
  peak_adc_60s: number | null;
  status: "WARMING UP" | "VALID" | "STALE" | "SENSOR FAULT" | string;
  response_level: "NORMAL" | "WARNING" | "CRITICAL" | string;
  baseline_capture_ready: boolean;
  baseline_capture_reason: string;
  trend_window_seconds: number;
  trend_deadband_adc: number;
  baseline_max_spread_adc: number;
  baseline_stability_sample_count: number;
  baseline_stability_span_adc: number | null;
};

export type TemperatureAlert = {
  status: "NORMAL" | "ALERT" | "STALE" | "UNAVAILABLE" | string;
  active: boolean;
  high_threshold: number;
  low_threshold: number;
  current_temperature: number | null;
  sensor_status: string;
  sound_active?: boolean;
};

export type GasAlert = {
  status: "NORMAL" | "WARNING" | "CRITICAL" | "SENSOR_FAULT" | "STALE" | "UNKNOWN" | string;
  active: boolean;
  level: "NORMAL" | "WARNING" | "CRITICAL" | string;
  warn_threshold_adc: number;
  warn_threshold_voltage: number;
  crit_threshold_adc: number;
  crit_threshold_voltage: number;
  clear_warn_adc: number;
  clear_crit_adc: number;
  current_adc: number | null;
  current_voltage: number | null;
  sensor_status: string;
  sound_active?: boolean;
};

export type HistoryRecord = {
  id?: number;
  timestamp: number;
  uptime_ms?: number | null;
  temperature?: number | null;
  humidity?: number | null;
  accel_x?: number | null;
  accel_y?: number | null;
  accel_z?: number | null;
  gyro_x?: number | null;
  gyro_y?: number | null;
  gyro_z?: number | null;
  gas_adc?: number | null;
  gas_voltage?: number | null;
  emergency_button?: string | null;
  dht_status?: string | null;
  mpu_status?: string | null;
};

export type PortInfo = { device: string; description: string; hwid?: string };

export type AlertEvent = {
  id: number | string;
  timestamp: number;
  time?: string;
  alert_type: string;
  event_type?: "TRIGGERED" | "CLEARED" | string;
  severity: "Emergency" | "Warning" | "Info";
  message: string;
  sensor_value?: number | null;
  threshold_value?: number | null;
  acknowledged: boolean;
};
