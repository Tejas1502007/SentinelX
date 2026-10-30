#include <Wire.h>
#include "DHT.h"
#include <math.h>

// ====================================================================
// SENTINEL-X — IoT Worker Safety & Emergency Alert System
// High-Performance 50 Hz IMU Sampling & USB Serial Telemetry Node
// ====================================================================

// --- 1. Pin Definitions ---
#define DHTPIN            4
#define DHTTYPE           DHT22
#define I2C_SDA           21
#define I2C_SCL           22
#define MQ135_PIN         34
#define EMERGENCY_PIN     27
#define BUZZER_PIN        25  // Primary buzzer pin
#define BUZZER_PIN_ALT    26  // Alternate buzzer pin (supports both wiring conventions)

// --- 2. Timing Targets ---
const unsigned long IMU_INTERVAL_US    = 20000;  // 50 Hz (20 ms nominal)
const unsigned long DHT_INTERVAL_MS    = 2000;   // 0.5 Hz (2000 ms)
const unsigned long MQ_INTERVAL_MS     = 1000;   // 1 Hz (1000 ms)
const unsigned long OUTPUT_INTERVAL_MS = 500;    // 2 Hz serial telemetry
const unsigned long WARMUP_TIME_MS     = 20000;  // 20s initial sensor stabilization

// --- 3. Sensor Drivers & Bus Config ---
DHT dht(DHTPIN, DHTTYPE);
uint8_t mpu_address = 0x68;

// --- 4. Sensor Status Strings ---
const char* STATUS_OK           = "ok";
const char* STATUS_ERROR        = "error";
const char* STATUS_STALE        = "stale";
const char* STATUS_INITIALIZING = "initializing";

// --- 5. Sensor State Variables ---
bool mpu_ready = false;
String mpu_status = STATUS_INITIALIZING;
String dht_status = STATUS_INITIALIZING;
String mq_status  = STATUS_INITIALIZING;

// --- Temperature Alert Threshold Definitions (Local Edge Evaluation) ---
const float TEMP_ALERT_HIGH_C = 31.0f; // Alert triggers strictly > 31.0°C
const float TEMP_ALERT_LOW_C  = 30.0f; // Alert clears <= 30.0°C
bool temp_alert_active = false;
String temp_alert_status = "NORMAL";

// --- Gas Alert Threshold Definitions (Local Edge Evaluation) ---
// Provisional demonstration thresholds (calibrated against clean air baseline ~50 ADC)
const int GAS_ALERT_WARN_ADC = 400;      // Warning triggers strictly >= 400 ADC (~0.32 V)
const int GAS_CLEAR_WARN_ADC = 350;      // Warning clears strictly < 350 ADC (~0.28 V) (50 ADC hysteresis)
const int GAS_ALERT_CRIT_ADC = 1000;     // Critical triggers strictly >= 1000 ADC (~0.81 V)
const int GAS_CLEAR_CRIT_ADC = 850;      // Critical clears strictly < 850 ADC (~0.68 V) (150 ADC hysteresis)

const float GAS_ALERT_WARN_VOLTAGE = (GAS_ALERT_WARN_ADC / 4095.0f) * 3.3f;
const float GAS_ALERT_CRIT_VOLTAGE = (GAS_ALERT_CRIT_ADC / 4095.0f) * 3.3f;

bool gas_alert_active = false;
String gas_alert_level = "NORMAL";       // "NORMAL", "WARNING", "CRITICAL"
String gas_alert_status = "NORMAL";      // "NORMAL", "WARNING", "CRITICAL", "SENSOR_FAULT", "STALE"
bool gas_crit_sounding = false;
bool gas_warn_sounding = false;
unsigned long gas_buzzer_start_ms = 0;

// --- Local Buzzer Policy States (Distinct: Alert State, Sound State, SOS State) ---
const unsigned long TEMP_BUZZER_DURATION_MS = 60000UL; // 60s timeout for temp alert buzzer
bool temp_buzzer_sounding = false;                     // Temperature alert sound state
unsigned long temp_buzzer_start_ms = 0;                // Timestamp when temp buzzer began sounding
bool buzzer_hardware_active = false;                   // Physical pin output state (HIGH/LOW)
const char* buzzer_active_source = "OFF";              // Active source: "SOS", "GAS_CRITICAL", "GAS_WARNING", "TEMP_ALERT", "OFF"

float current_temperature = NAN;
float current_humidity    = NAN;
unsigned long last_dht_read_ms = 0;
unsigned long last_dht_success_ms = 0;

int current_gas_adc       = 0;
float current_gas_voltage = 0.0;
unsigned long last_mq_read_ms = 0;
unsigned long last_mq_success_ms = 0;

bool emergency_pressed = false;

// --- 6. IMU State & Diagnostics ---
float current_accel_x = NAN;
float current_accel_y = NAN;
float current_accel_z = NAN;
float current_gyro_x  = NAN;
float current_gyro_y  = NAN;
float current_gyro_z  = NAN;

float current_accel_mag_mps2 = NAN;
float current_accel_mag_g    = NAN;
float current_gyro_mag_rads  = NAN;

uint32_t imu_sample_count       = 0;
uint32_t imu_missed_deadlines   = 0;
uint32_t imu_sample_interval_us = 0;
unsigned long last_imu_sample_us = 0;
unsigned long imu_last_sample_ms = 0;
unsigned long next_imu_deadline_us = 0;

// --- 7. 1-Second Rolling Window Buffer (50 samples capacity) ---
#define WINDOW_CAPACITY 50

struct IMUWindowSample {
  unsigned long timestamp_us;
  float accel_mag_mps2;
  float gyro_mag_rads;
  bool valid;
};

IMUWindowSample window_buffer[WINDOW_CAPACITY];
int window_head = 0;
int window_count = 0;

// Window Features
int window_valid_samples = 0;
unsigned long window_duration_ms = 0;
float window_accel_mean_mps2 = NAN;
float window_accel_peak_mps2 = NAN;
float window_accel_std_mps2  = NAN;
float window_gyro_mean_rads  = NAN;
float window_gyro_peak_rads  = NAN;
bool window_complete = false;

// Telemetry output timer
unsigned long last_output_ms = 0;

// ====================================================================
// MPU6050 DIRECT REGISTER I2C DRIVER (Clone-Resilient & Zero-Dependency)
// ====================================================================

bool writeMPURegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(mpu_address);
  Wire.write(reg);
  Wire.write(value);
  return (Wire.endTransmission() == 0);
}

bool readMPURegisters(uint8_t startReg, uint8_t *buffer, uint8_t length) {
  Wire.beginTransmission(mpu_address);
  Wire.write(startReg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }
  uint8_t received = Wire.requestFrom((uint16_t)mpu_address, (size_t)length, (bool)true);
  if (received != length) {
    while (Wire.available()) {
      Wire.read();
    }
    return false;
  }
  for (uint8_t i = 0; i < length; i++) {
    buffer[i] = Wire.read();
  }
  return true;
}

inline int16_t combineBytes(uint8_t highByte, uint8_t lowByte) {
  return (int16_t)((highByte << 8) | lowByte);
}

// Scans the I2C bus to find if MPU6050 is on 0x68 or 0x69
uint8_t scanI2CForMPU() {
  uint8_t found_addr = 0;
  for (uint8_t addr = 0x68; addr <= 0x69; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      found_addr = addr;
      break;
    }
  }
  return found_addr;
}

bool tryInitMPUAtAddress(uint8_t addr) {
  mpu_address = addr;

  // Wake the MPU6050 (clears sleep mode bit 6 in PWR_MGMT_1 register 0x6B)
  bool wakeOK = writeMPURegister(0x6B, 0x00);
  delay(100);

  // Accelerometer range: +/- 2 g (16384 LSB/g)
  bool accelOK = writeMPURegister(0x1C, 0x00);

  // Gyroscope range: +/- 250 deg/s (131 LSB/(deg/s))
  bool gyroOK = writeMPURegister(0x1B, 0x00);

  // Digital Low Pass Filter ~44Hz (CONFIG register 0x1A = 0x03)
  writeMPURegister(0x1A, 0x03);

  // Read WHO_AM_I register (0x75)
  uint8_t identity = 0;
  bool readOK = readMPURegisters(0x75, &identity, 1);

  if (wakeOK && accelOK && gyroOK && readOK) {
    Serial.printf("[MPU6050] Direct I2C connected at 0x%02X! WHO_AM_I = 0x%02X\n", mpu_address, identity);
    return true;
  }
  return false;
}

bool initializeMPU() {
  uint8_t detected = scanI2CForMPU();
  if (detected != 0) {
    if (tryInitMPUAtAddress(detected)) return true;
  }
  // Fallback checks
  if (tryInitMPUAtAddress(0x68)) return true;
  if (tryInitMPUAtAddress(0x69)) return true;
  return false;
}

// ====================================================================
// SENSOR ACQUISITION FUNCTIONS
// ====================================================================

void sampleIMU() {
  unsigned long now_us = micros();
  unsigned long now_ms = millis();

  bool read_ok = false;
  float ax = 0, ay = 0, az = 0;
  float gx = 0, gy = 0, gz = 0;

  if (mpu_ready) {
    uint8_t data[14];
    if (readMPURegisters(0x3B, data, 14)) {
      int16_t axRaw = combineBytes(data[0], data[1]);
      int16_t ayRaw = combineBytes(data[2], data[3]);
      int16_t azRaw = combineBytes(data[4], data[5]);
      // data[6], data[7] = internal chip temperature
      int16_t gxRaw = combineBytes(data[8], data[9]);
      int16_t gyRaw = combineBytes(data[10], data[11]);
      int16_t gzRaw = combineBytes(data[12], data[13]);

      // Accelerometer: +/- 2 g, 16384 LSB/g -> convert to m/s^2
      ax = (axRaw / 16384.0f) * 9.80665f;
      ay = (ayRaw / 16384.0f) * 9.80665f;
      az = (azRaw / 16384.0f) * 9.80665f;

      // Gyroscope: +/- 250 deg/s, 131 LSB/(deg/s) -> convert to rad/s
      const float DEG_TO_RAD_VALUE = 0.01745329252f;
      gx = (gxRaw / 131.0f) * DEG_TO_RAD_VALUE;
      gy = (gyRaw / 131.0f) * DEG_TO_RAD_VALUE;
      gz = (gzRaw / 131.0f) * DEG_TO_RAD_VALUE;

      if (isfinite(ax) && isfinite(ay) && isfinite(az) &&
          isfinite(gx) && isfinite(gy) && isfinite(gz)) {
        read_ok = true;
      }
    }
  }

  if (read_ok) {
    if (last_imu_sample_us > 0) {
      imu_sample_interval_us = now_us - last_imu_sample_us;
    } else {
      imu_sample_interval_us = IMU_INTERVAL_US;
    }
    last_imu_sample_us = now_us;
    imu_last_sample_ms = now_ms;
    imu_sample_count++;
    mpu_status = STATUS_OK;

    current_accel_x = ax;
    current_accel_y = ay;
    current_accel_z = az;
    current_gyro_x  = gx;
    current_gyro_y  = gy;
    current_gyro_z  = gz;

    // Derived instantaneous motion parameters
    current_accel_mag_mps2 = sqrt(current_accel_x * current_accel_x +
                                  current_accel_y * current_accel_y +
                                  current_accel_z * current_accel_z);
    current_accel_mag_g    = current_accel_mag_mps2 / 9.80665f;
    current_gyro_mag_rads  = sqrt(current_gyro_x * current_gyro_x +
                                  current_gyro_y * current_gyro_y +
                                  current_gyro_z * current_gyro_z);

    // Push into rolling window circular buffer
    window_buffer[window_head].timestamp_us   = now_us;
    window_buffer[window_head].accel_mag_mps2 = current_accel_mag_mps2;
    window_buffer[window_head].gyro_mag_rads  = current_gyro_mag_rads;
    window_buffer[window_head].valid          = true;

    window_head = (window_head + 1) % WINDOW_CAPACITY;
    if (window_count < WINDOW_CAPACITY) {
      window_count++;
    }
  } else {
    // Communication or sensor range error
    mpu_status = STATUS_ERROR;
    current_accel_x = NAN;
    current_accel_y = NAN;
    current_accel_z = NAN;
    current_gyro_x  = NAN;
    current_gyro_y  = NAN;
    current_gyro_z  = NAN;
    current_accel_mag_mps2 = NAN;
    current_accel_mag_g    = NAN;
    current_gyro_mag_rads  = NAN;

    // Record invalid sample in buffer
    window_buffer[window_head].timestamp_us = now_us;
    window_buffer[window_head].valid = false;
    window_head = (window_head + 1) % WINDOW_CAPACITY;
    if (window_count < WINDOW_CAPACITY) {
      window_count++;
    }
  }
}

void computeWindowFeatures() {
  unsigned long now_us = micros();
  const unsigned long ONE_SEC_US = 1000000;

  int count = 0;
  float sum_accel = 0.0f;
  float peak_accel = 0.0f;
  float sum_gyro = 0.0f;
  float peak_gyro = 0.0f;

  unsigned long oldest_ts = 0;
  unsigned long newest_ts = 0;
  unsigned long max_gap_us = 0;
  unsigned long prev_ts = 0;

  for (int i = 0; i < window_count; i++) {
    int idx = (window_head - 1 - i + WINDOW_CAPACITY) % WINDOW_CAPACITY;
    unsigned long sample_age_us = now_us - window_buffer[idx].timestamp_us;
    if (sample_age_us > ONE_SEC_US) {
      break;
    }

    if (window_buffer[idx].valid) {
      float am = window_buffer[idx].accel_mag_mps2;
      float gm = window_buffer[idx].gyro_mag_rads;

      sum_accel += am;
      if (count == 0 || am > peak_accel) peak_accel = am;

      sum_gyro += gm;
      if (count == 0 || gm > peak_gyro) peak_gyro = gm;

      if (count == 0) {
        newest_ts = window_buffer[idx].timestamp_us;
      }
      oldest_ts = window_buffer[idx].timestamp_us;

      if (prev_ts > 0) {
        unsigned long gap = prev_ts - window_buffer[idx].timestamp_us;
        if (gap > max_gap_us) max_gap_us = gap;
      }
      prev_ts = window_buffer[idx].timestamp_us;

      count++;
    }
  }

  window_valid_samples = count;

  if (count >= 2) {
    window_duration_ms = (newest_ts - oldest_ts) / 1000;
    window_accel_mean_mps2 = sum_accel / count;
    window_accel_peak_mps2 = peak_accel;
    window_gyro_mean_rads  = sum_gyro / count;
    window_gyro_peak_rads  = peak_gyro;

    float variance_sum = 0.0f;
    for (int i = 0; i < window_count; i++) {
      int idx = (window_head - 1 - i + WINDOW_CAPACITY) % WINDOW_CAPACITY;
      if (now_us - window_buffer[idx].timestamp_us > ONE_SEC_US) break;
      if (window_buffer[idx].valid) {
        float diff = window_buffer[idx].accel_mag_mps2 - window_accel_mean_mps2;
        variance_sum += (diff * diff);
      }
    }
    window_accel_std_mps2 = sqrt(variance_sum / (count - 1));
    window_complete = (count >= 40 && window_duration_ms >= 800 && max_gap_us < 100000);
  } else {
    window_duration_ms = 0;
    window_accel_mean_mps2 = NAN;
    window_accel_peak_mps2 = NAN;
    window_accel_std_mps2  = NAN;
    window_gyro_mean_rads  = NAN;
    window_gyro_peak_rads  = NAN;
    window_complete = false;
  }
}

void sampleDHT() {
  last_dht_read_ms = millis();
  float h = dht.readHumidity();
  float t = dht.readTemperature();

  if (!isnan(t) && !isnan(h) && t >= -40.0f && t <= 80.0f && h >= 0.0f && h <= 100.0f) {
    current_temperature = t;
    current_humidity    = h;
    dht_status = STATUS_OK;
    last_dht_success_ms = last_dht_read_ms;

    // Hysteresis Temperature Threshold Evaluation
    if (current_temperature > TEMP_ALERT_HIGH_C) {
      if (!temp_alert_active) {
        // Rule 2: When a NEW valid temperature alert starts above 31°C, sound buzzer for up to 60s
        temp_alert_active = true;
        temp_alert_status = "ALERT";
        temp_buzzer_sounding = true;
        temp_buzzer_start_ms = millis();
        Serial.print(F("[BUZZER] NEW Temperature Alert triggered: "));
        Serial.print(current_temperature, 1);
        Serial.println(F("°C (>31.0°C). Buzzer 60s timer started."));
      } else {
        // Rule 4: Do not restart 60s timer repeatedly while the same alert remains active
        temp_alert_status = "ALERT";
      }
    } else if (current_temperature <= TEMP_ALERT_LOW_C) {
      // Rule 5: Clear temperature alert at or below 30°C
      if (temp_alert_active) {
        temp_alert_active = false;
        temp_alert_status = "NORMAL";
        temp_buzzer_sounding = false; // Disarm temp buzzer if active
        Serial.print(F("[BUZZER] Temperature normalized: "));
        Serial.print(current_temperature, 1);
        Serial.println(F("°C (<=30.0°C). Alert CLEARED, buzzer disarmed."));
      } else {
        temp_alert_status = "NORMAL";
      }
    }
    // In hysteresis band (30.0 < temp <= 31.0): maintain current alert state without restarting buzzer
  } else {
    dht_status = STATUS_ERROR;
    // Missing/invalid reading: Never clear an active alert on invalid data!
    if (temp_alert_active) {
      temp_alert_status = "ALERT";
      Serial.println(F("[BUZZER] DHT22 reading invalid/error. Retaining ACTIVE temperature alert."));
    } else {
      temp_alert_status = "UNAVAILABLE";
    }
  }
}

void sampleMQ() {
  last_mq_read_ms = millis();

  // Multi-sample ADC 16 times to filter ADC electrical noise on ESP32
  uint32_t adc_sum = 0;
  for (int i = 0; i < 16; i++) {
    adc_sum += analogRead(MQ135_PIN);
  }
  int avg_adc = (int)(adc_sum / 16);

  // Sensor warm-up evaluation (first 20s initial stabilization)
  if (last_mq_read_ms < WARMUP_TIME_MS) {
    mq_status = "warming_up";
  } else {
    mq_status = STATUS_OK;
  }

  current_gas_adc = avg_adc;
  current_gas_voltage = (current_gas_adc / 4095.0f) * 3.3f;
  last_mq_success_ms = last_mq_read_ms;

  // Local edge threshold evaluation with hysteresis
  if (mq_status == STATUS_OK) {
    if (current_gas_adc >= GAS_ALERT_CRIT_ADC) {
      if (gas_alert_level != "CRITICAL") {
        Serial.print(F("[BUZZER] CRITICAL Gas Threshold Exceeded: ADC "));
        Serial.print(current_gas_adc);
        Serial.print(F(" ("));
        Serial.print(current_gas_voltage, 2);
        Serial.println(F(" V >= 1000). Priority: CRITICAL GAS."));
        gas_buzzer_start_ms = millis();
      }
      gas_alert_active = true;
      gas_alert_level = "CRITICAL";
      gas_alert_status = "CRITICAL";
      gas_crit_sounding = true;
      gas_warn_sounding = false;
    } else if (gas_alert_level == "CRITICAL") {
      // Hysteresis for Critical Alert: clears below GAS_CLEAR_CRIT_ADC (850)
      if (current_gas_adc < GAS_CLEAR_CRIT_ADC) {
        if (current_gas_adc >= GAS_ALERT_WARN_ADC) {
          gas_alert_level = "WARNING";
          gas_alert_status = "WARNING";
          gas_crit_sounding = false;
          gas_warn_sounding = true;
          Serial.print(F("[BUZZER] Gas concentration de-escalated to WARNING: ADC "));
          Serial.print(current_gas_adc);
          Serial.println(F(" (< 850 clear threshold)."));
        } else if (current_gas_adc < GAS_CLEAR_WARN_ADC) {
          gas_alert_active = false;
          gas_alert_level = "NORMAL";
          gas_alert_status = "NORMAL";
          gas_crit_sounding = false;
          gas_warn_sounding = false;
          Serial.print(F("[BUZZER] Gas concentration normalized: ADC "));
          Serial.print(current_gas_adc);
          Serial.println(F(" (< 350 clear threshold). Gas alert CLEARED."));
        }
      }
      // In critical hysteresis band (850 <= adc < 1000): maintain CRITICAL
    } else if (current_gas_adc >= GAS_ALERT_WARN_ADC) {
      if (gas_alert_level != "WARNING") {
        Serial.print(F("[BUZZER] WARNING Gas Threshold Exceeded: ADC "));
        Serial.print(current_gas_adc);
        Serial.print(F(" ("));
        Serial.print(current_gas_voltage, 2);
        Serial.println(F(" V >= 400). Gas warning beeping pattern started."));
        gas_buzzer_start_ms = millis();
      }
      gas_alert_active = true;
      gas_alert_level = "WARNING";
      gas_alert_status = "WARNING";
      gas_crit_sounding = false;
      gas_warn_sounding = true;
    } else if (gas_alert_level == "WARNING") {
      // Hysteresis for Warning Alert: clears below GAS_CLEAR_WARN_ADC (350)
      if (current_gas_adc < GAS_CLEAR_WARN_ADC) {
        gas_alert_active = false;
        gas_alert_level = "NORMAL";
        gas_alert_status = "NORMAL";
        gas_crit_sounding = false;
        gas_warn_sounding = false;
        Serial.print(F("[BUZZER] Gas concentration normalized: ADC "));
        Serial.print(current_gas_adc);
        Serial.println(F(" (< 350 clear threshold). Gas alert CLEARED."));
      }
      // In warning hysteresis band (350 <= adc < 400): maintain WARNING
    } else {
      gas_alert_active = false;
      gas_alert_level = "NORMAL";
      gas_alert_status = "NORMAL";
      gas_crit_sounding = false;
      gas_warn_sounding = false;
    }
  } else {
    // Missing, invalid, or stale: Never clear an active alert on invalid data!
    if (gas_alert_active) {
      gas_alert_status = gas_alert_level;
      Serial.println(F("[BUZZER] MQ-135 reading invalid/warming up. Retaining ACTIVE gas alert."));
    } else {
      gas_alert_status = "SENSOR_FAULT";
    }
  }
}

void sampleEmergencyButton() {
  // Active-LOW input with internal pull-up (GPIO27)
  bool reading = (digitalRead(EMERGENCY_PIN) == LOW);
  if (reading != emergency_pressed) {
    if (reading) {
      Serial.println(F("[BUZZER] SOS BUTTON PRESSED! Local buzzer IMMEDIATE ON (Priority: SOS)."));
    } else {
      Serial.print(F("[BUZZER] SOS button released. "));
      if (gas_crit_sounding) {
        Serial.println(F("Resuming Critical Gas Alert buzzer pattern."));
      } else if (gas_warn_sounding) {
        Serial.println(F("Resuming Gas Warning buzzer pattern."));
      } else if (temp_buzzer_sounding) {
        Serial.println(F("Resuming Temperature Alert beeping pattern."));
      } else {
        Serial.println(F("Buzzer silenced."));
      }
    }
    emergency_pressed = reading;
  }
}

void updateLocalAlerts() {
  unsigned long now_ms = millis();

  // Rule 3: After 60 seconds, silence temperature buzzer while keeping temp alert active
  if (temp_buzzer_sounding) {
    if (now_ms - temp_buzzer_start_ms >= TEMP_BUZZER_DURATION_MS) {
      temp_buzzer_sounding = false;
      Serial.println(F("[BUZZER] Temperature buzzer 60s timeout elapsed. Buzzer SILENCED. Temperature alert remains ACTIVE on dashboard."));
    }
  }

  // Priority Arbitration:
  // Priority 1: Pressing SOS activates buzzer immediately and takes priority over every other alert (continuous tone).
  // Priority 2: Critical Gas Alert (urgent rapid 5 Hz pulse: 100ms ON / 100ms OFF). Higher priority than Temperature!
  // Priority 3: Gas Warning Alert (intermittent 2 Hz beep: 150ms ON / 350ms OFF). Higher priority than Temperature!
  // Priority 4: Temperature Alert (250ms ON / 250ms OFF pulse with 60s timeout).
  // Priority 5: Standby / Off (silent).
  if (emergency_pressed) {
    buzzer_hardware_active = true; // Continuous tone for urgent SOS
    buzzer_active_source = "SOS";
  } else if (gas_alert_active && gas_alert_level == "CRITICAL" && gas_crit_sounding) {
    // Non-blocking rapid pulse for Critical Gas (100ms ON / 100ms OFF = 5 Hz urgent cadence)
    bool pulse = (((now_ms - gas_buzzer_start_ms) / 100) % 2 == 0);
    buzzer_hardware_active = pulse;
    buzzer_active_source = "GAS_CRITICAL";
  } else if (gas_alert_active && gas_alert_level == "WARNING" && gas_warn_sounding) {
    // Non-blocking intermittent beep for Gas Warning (150ms ON / 350ms OFF = 2 Hz cadence)
    bool pulse = (((now_ms - gas_buzzer_start_ms) % 500) < 150);
    buzzer_hardware_active = pulse;
    buzzer_active_source = "GAS_WARNING";
  } else if (temp_buzzer_sounding) {
    // Non-blocking pattern for temperature alert (250ms ON / 250ms OFF = 2 Hz intermittent pulse)
    bool pulse = (((now_ms - temp_buzzer_start_ms) / 250) % 2 == 0);
    buzzer_hardware_active = pulse;
    buzzer_active_source = "TEMP_ALERT";
  } else {
    buzzer_hardware_active = false;
    buzzer_active_source = "OFF";
  }

  // Drive primary buzzer pin (GPIO25) and alternate buzzer pin (GPIO26)
  if (BUZZER_PIN >= 0) {
    digitalWrite(BUZZER_PIN, buzzer_hardware_active ? HIGH : LOW);
  }
  if (BUZZER_PIN_ALT >= 0) {
    digitalWrite(BUZZER_PIN_ALT, buzzer_hardware_active ? HIGH : LOW);
  }
}

// Helper to format float values or null for JSON
String formatFloatOrNull(float val, int precision) {
  if (isnan(val)) {
    return "null";
  }
  return String(val, precision);
}

// ====================================================================
// JSON EMISSION (USB SERIAL ONLY)
// ====================================================================

void emitTelemetryJSON() {
  unsigned long now_ms = millis();

  if (now_ms - last_dht_success_ms > 5000 && dht_status == STATUS_OK) {
    dht_status = STATUS_STALE;
  }
  if (now_ms - last_mq_success_ms > 3000 && mq_status == STATUS_OK) {
    mq_status = STATUS_STALE;
  }
  if (now_ms - imu_last_sample_ms > 200 && mpu_status == STATUS_OK) {
    mpu_status = STATUS_STALE;
  }

  // Preserve alert state even if DHT reading becomes stale
  if (temp_alert_active) {
    temp_alert_status = "ALERT";
  } else if (dht_status == STATUS_STALE || dht_status == STATUS_ERROR) {
    temp_alert_status = "STALE";
  }

  // Preserve gas alert state even if MQ reading becomes stale
  if (gas_alert_active) {
    gas_alert_status = gas_alert_level;
  } else if (mq_status == STATUS_STALE || mq_status == STATUS_ERROR) {
    gas_alert_status = "STALE";
  }

  computeWindowFeatures();

  String json = "";
  json.reserve(768);

  json += "{";
  json += "\"uptime_ms\":";
  json += String(now_ms);

  json += ",\"dht_status\":\"";
  json += dht_status;
  json += "\"";

  json += ",\"mpu_status\":\"";
  json += mpu_status;
  json += "\"";

  json += ",\"temperature\":";
  json += formatFloatOrNull(current_temperature, 1);

  json += ",\"humidity\":";
  json += formatFloatOrNull(current_humidity, 1);

  json += ",\"accel\":{";
  json += "\"x\":";
  json += formatFloatOrNull(current_accel_x, 3);
  json += ",\"y\":";
  json += formatFloatOrNull(current_accel_y, 3);
  json += ",\"z\":";
  json += formatFloatOrNull(current_accel_z, 3);
  json += "}";

  json += ",\"gyro\":{";
  json += "\"x\":";
  json += formatFloatOrNull(current_gyro_x, 4);
  json += ",\"y\":";
  json += formatFloatOrNull(current_gyro_y, 4);
  json += ",\"z\":";
  json += formatFloatOrNull(current_gyro_z, 4);
  json += "}";

  json += ",\"gas_adc\":";
  json += String(current_gas_adc);

  json += ",\"gas_voltage_est\":";
  json += formatFloatOrNull(current_gas_voltage, 3);

  json += ",\"emergency_button\":\"";
  json += (emergency_pressed ? "PRESSED" : "RELEASED");
  json += "\"";

  json += ",\"buzzer\":\"";
  json += (emergency_pressed || gas_crit_sounding || gas_warn_sounding || temp_buzzer_sounding ? "ACTIVE" : "STANDBY");
  json += "\"";

  json += ",\"buzzer_mode\":\"";
  json += buzzer_active_source;
  json += "\"";

  // Temperature Alert Threshold State (Edge-evaluated)
  json += ",\"temperature_alert\":{";
  json += "\"status\":\"";
  json += temp_alert_status;
  json += "\",\"active\":";
  json += (temp_alert_active ? "true" : "false");
  json += ",\"high_threshold\":31.0";
  json += ",\"low_threshold\":30.0";
  json += ",\"sound_active\":";
  json += (temp_buzzer_sounding ? "true" : "false");
  json += "}";

  // Gas Alert Threshold State (Edge-evaluated)
  json += ",\"gas_alert\":{";
  json += "\"status\":\"";
  json += gas_alert_status;
  json += "\",\"active\":";
  json += (gas_alert_active ? "true" : "false");
  json += ",\"level\":\"";
  json += gas_alert_level;
  json += "\",\"warn_threshold_adc\":";
  json += String(GAS_ALERT_WARN_ADC);
  json += ",\"warn_threshold_voltage\":";
  json += String(GAS_ALERT_WARN_VOLTAGE, 3);
  json += ",\"crit_threshold_adc\":";
  json += String(GAS_ALERT_CRIT_ADC);
  json += ",\"crit_threshold_voltage\":";
  json += String(GAS_ALERT_CRIT_VOLTAGE, 3);
  json += ",\"clear_warn_adc\":";
  json += String(GAS_CLEAR_WARN_ADC);
  json += ",\"clear_crit_adc\":";
  json += String(GAS_CLEAR_CRIT_ADC);
  json += ",\"sound_active\":";
  json += (gas_crit_sounding || gas_warn_sounding ? "true" : "false");
  json += "}";

  json += ",\"motion\":{";
  json += "\"accel_magnitude_mps2\":";
  json += formatFloatOrNull(current_accel_mag_mps2, 3);
  json += ",\"accel_magnitude_g\":";
  json += formatFloatOrNull(current_accel_mag_g, 3);
  json += ",\"gyro_magnitude_rads\":";
  json += formatFloatOrNull(current_gyro_mag_rads, 4);
  json += ",\"window_samples\":";
  json += String(window_valid_samples);
  json += ",\"window_duration_ms\":";
  json += String(window_duration_ms);
  json += ",\"accel_mag_mean_mps2\":";
  json += formatFloatOrNull(window_accel_mean_mps2, 3);
  json += ",\"accel_mag_peak_mps2\":";
  json += formatFloatOrNull(window_accel_peak_mps2, 3);
  json += ",\"accel_mag_std_mps2\":";
  json += formatFloatOrNull(window_accel_std_mps2, 3);
  json += ",\"gyro_mag_mean_rads\":";
  json += formatFloatOrNull(window_gyro_mean_rads, 4);
  json += ",\"gyro_mag_peak_rads\":";
  json += formatFloatOrNull(window_gyro_peak_rads, 4);
  json += ",\"window_complete\":";
  json += (window_complete ? "true" : "false");
  json += "}";

  json += ",\"diagnostics\":{";
  json += "\"imu_sample_count\":";
  json += String(imu_sample_count);
  json += ",\"imu_sample_interval_us\":";
  json += String(imu_sample_interval_us);
  json += ",\"imu_missed_deadlines\":";
  json += String(imu_missed_deadlines);
  json += ",\"imu_last_sample_ms\":";
  json += String(imu_last_sample_ms);
  json += ",\"dht_sample_age_ms\":";
  json += String(now_ms - last_dht_success_ms);
  json += ",\"mq_sample_age_ms\":";
  json += String(now_ms - last_mq_success_ms);
  json += ",\"mq_status\":\"";
  json += mq_status;
  json += "\"";
  json += "}";

  json += "}";

  // Output directly over USB Serial (COM5 @ 115200 baud)
  Serial.println(json);
}

// ====================================================================
// SETUP & LOOP
// ====================================================================

void setup() {
  Serial.begin(115200);

  // Configure Emergency SOS Button with internal pull-up
  pinMode(EMERGENCY_PIN, INPUT_PULLUP);

  // Configure local buzzer output
  if (BUZZER_PIN >= 0) {
    pinMode(BUZZER_PIN, OUTPUT);
    digitalWrite(BUZZER_PIN, LOW);
  }
  if (BUZZER_PIN_ALT >= 0) {
    pinMode(BUZZER_PIN_ALT, OUTPUT);
    digitalWrite(BUZZER_PIN_ALT, LOW);
  }

  // Configure ADC for MQ-135 on GPIO 34
  analogReadResolution(12);
  analogSetPinAttenuation(MQ135_PIN, ADC_11db);

  // Initialize I2C bus at 100 kHz (Standard Mode)
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);

  // Initialize DHT22
  dht.begin();

  // Allow sensor oscillator and power rail to stabilize
  delay(200);

  // Initialize MPU6050 direct I2C communication
  if (initializeMPU()) {
    mpu_ready = true;
    mpu_status = STATUS_OK;
    Serial.println("[MPU6050] Initialized successfully via Direct I2C!");
  } else {
    mpu_ready = false;
    mpu_status = STATUS_ERROR;
    Serial.println("[MPU6050] Sensor not responding at 0x68/0x69, will auto-retry in loop...");
  }

  // Pre-seed window buffer
  for (int i = 0; i < WINDOW_CAPACITY; i++) {
    window_buffer[i].valid = false;
  }

  // Initial sensor reads
  sampleDHT();
  sampleMQ();

  // Initialize deadline scheduler
  next_imu_deadline_us = micros() + IMU_INTERVAL_US;
  last_output_ms = millis();
}

void loop() {
  unsigned long now_us = micros();
  unsigned long now_ms = millis();

  // 1. SOS Button & Local Buzzer: Poll continuously on every loop iteration
  sampleEmergencyButton();
  updateLocalAlerts();

  // 2. Dynamic MPU6050 Auto-Detection Retry (every 2.5s if not ready)
  static unsigned long last_mpu_retry_ms = 0;
  if (!mpu_ready && (now_ms - last_mpu_retry_ms >= 2500)) {
    last_mpu_retry_ms = now_ms;
    if (initializeMPU()) {
      mpu_ready = true;
      mpu_status = STATUS_OK;
      Serial.println("[MPU6050] Connected dynamically via Direct I2C!");
    }
  }

  // 3. Controlled 50 Hz IMU Sampling (Deadline-based monotonic scheduler)
  if ((long)(now_us - next_imu_deadline_us) >= 0) {
    unsigned long lateness_us = now_us - next_imu_deadline_us;

    if (lateness_us > IMU_INTERVAL_US) {
      unsigned long missed = lateness_us / IMU_INTERVAL_US;
      imu_missed_deadlines += missed;
      next_imu_deadline_us = now_us + IMU_INTERVAL_US;
    } else {
      next_imu_deadline_us += IMU_INTERVAL_US;
    }

    sampleIMU();
  }

  // 4. MQ-135 Sampling (1 Hz nominal)
  if (now_ms - last_mq_read_ms >= MQ_INTERVAL_MS) {
    sampleMQ();
  }

  // 5. DHT22 Sampling (0.5 Hz nominal / 2000 ms)
  if (now_ms - last_dht_read_ms >= DHT_INTERVAL_MS) {
    sampleDHT();
  }

  // 6. Telemetry Transmission over USB Serial (2 Hz / 500 ms nominal)
  if (now_ms - last_output_ms >= OUTPUT_INTERVAL_MS) {
    emitTelemetryJSON();
    last_output_ms = now_ms;
  }
}
