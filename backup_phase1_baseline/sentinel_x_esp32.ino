#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include "DHT.h"

// =====================================================
// SENTINEL-X: ESP32 Wearable Worker Safety Node Firmware
// Sensors:
//   - DHT22 (Temp & Humidity) -> GPIO 4
//   - MPU6050 (Accel & Gyro)  -> I2C (SDA: GPIO 21, SCL: GPIO 22)
//   - MQ-135 Gas Sensor       -> Analog GPIO 34 (ADC1)
//   - Emergency SOS Button    -> GPIO 27 (INPUT_PULLUP)
// Baud rate: 115200
// Output: Newline-delimited JSON
// =====================================================

// --- Pin Definitions ---
#define DHTPIN            4
#define DHTTYPE           DHT22
#define I2C_SDA           21
#define I2C_SCL           22
#define MQ135_PIN         34
#define EMERGENCY_PIN     27

DHT dht(DHTPIN, DHTTYPE);
Adafruit_MPU6050 mpu;

bool mpu_ready = false;
unsigned long last_sample_time = 0;
const unsigned long SAMPLE_INTERVAL_MS = 500; // Emit telemetry every 500ms

void setup() {
  Serial.begin(115200);

  // Configure Emergency Button (Active LOW with internal pull-up)
  pinMode(EMERGENCY_PIN, INPUT_PULLUP);

  // Configure ADC for MQ-135 (GPIO 34 is on ADC1, safe with Wi-Fi)
  analogReadResolution(12);
  analogSetPinAttenuation(MQ135_PIN, ADC_11db);

  // Initialize I2C with specified ESP32 pins
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(100000);

  // Initialize DHT22
  dht.begin();

  // Initialize MPU6050
  if (mpu.begin(0x68, &Wire)) {
    mpu_ready = true;
    mpu.setAccelerometerRange(MPU6050_RANGE_4_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  } else {
    mpu_ready = false;
  }

  delay(500);
}

void loop() {
  unsigned long current_time = millis();

  // Non-blocking telemetry loop
  if (current_time - last_sample_time >= SAMPLE_INTERVAL_MS) {
    last_sample_time = current_time;

    // --- 1. Read DHT22 ---
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature();
    bool dht_valid = !isnan(humidity) && !isnan(temperature);

    // --- 2. Read MPU6050 ---
    sensors_event_t a, g, temp;
    bool mpu_valid = false;
    if (mpu_ready && mpu.getEvent(&a, &g, &temp)) {
      mpu_valid = true;
    }

    // --- 3. Read MQ-135 Gas Sensor ---
    // Average 8 samples to reduce ADC switching noise
    int gas_sum = 0;
    for (int i = 0; i < 8; i++) {
      gas_sum += analogRead(MQ135_PIN);
      delayMicroseconds(50);
    }
    int gas_adc = gas_sum / 8;
    float gas_voltage = (gas_adc / 4095.0f) * 3.3f;

    // --- 4. Read Emergency Button ---
    bool emergency_pressed = (digitalRead(EMERGENCY_PIN) == LOW);

    // --- 5. Build & Emit JSON Packet ---
    Serial.print("{\"uptime_ms\":");
    Serial.print(current_time);

    // Sensor Status
    Serial.print(",\"dht_status\":\"");
    Serial.print(dht_valid ? "ok" : "error");
    Serial.print("\",\"mpu_status\":\"");
    Serial.print(mpu_valid ? "ok" : "error");
    Serial.print("\"");

    // Temperature & Humidity
    Serial.print(",\"temperature\":");
    if (dht_valid) {
      Serial.print(temperature, 1);
    } else {
      Serial.print("null");
    }

    Serial.print(",\"humidity\":");
    if (dht_valid) {
      Serial.print(humidity, 1);
    } else {
      Serial.print("null");
    }

    // Accelerometer (m/s^2)
    Serial.print(",\"accel\":");
    if (mpu_valid) {
      Serial.print("{\"x\":");
      Serial.print(a.acceleration.x, 3);
      Serial.print(",\"y\":");
      Serial.print(a.acceleration.y, 3);
      Serial.print(",\"z\":");
      Serial.print(a.acceleration.z, 3);
      Serial.print("}");
    } else {
      Serial.print("null");
    }

    // Gyroscope (rad/s)
    Serial.print(",\"gyro\":");
    if (mpu_valid) {
      Serial.print("{\"x\":");
      Serial.print(g.gyro.x, 4);
      Serial.print(",\"y\":");
      Serial.print(g.gyro.y, 4);
      Serial.print(",\"z\":");
      Serial.print(g.gyro.z, 4);
      Serial.print("}");
    } else {
      Serial.print("null");
    }

    // Gas Sensor (Raw ADC & estimated voltage)
    Serial.print(",\"gas_adc\":");
    Serial.print(gas_adc);

    Serial.print(",\"gas_voltage_est\":");
    Serial.print(gas_voltage, 3);

    // Emergency SOS Button
    Serial.print(",\"emergency_button\":\"");
    Serial.print(emergency_pressed ? "PRESSED" : "RELEASED");
    Serial.print("\"");

    // Terminate single line packet
    Serial.println("}");
  }
}
