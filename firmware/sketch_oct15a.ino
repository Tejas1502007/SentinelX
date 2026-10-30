#include <Wire.h>
#include <DHT.h>
#include <math.h>

// =====================================================
// SENTINEL-X: IoT Worker Safety and Emergency Alert
// ESP32 + DHT22 + MPU6050-compatible sensor
//       + MQ-135 analog output + Emergency Button
// =====================================================

// ---------------- PIN CONFIGURATION -----------------
#define DHT_PIN          4
#define DHT_TYPE         DHT22

#define MPU_SDA          21
#define MPU_SCL          22
#define MPU_ADDRESS      0x68

#define MQ135_PIN        34
#define EMERGENCY_PIN    27

// Reserved for future buzzer-driver integration.
// Do not connect the 12V buzzer directly to this pin.
#define BUZZER_CONTROL_PIN 26

DHT dht(DHT_PIN, DHT_TYPE);

// ---------------- SENSOR STATUS ---------------------
bool mpuReady = false;

float temperatureC = NAN;
float humidityRH = NAN;

float accelX = NAN;
float accelY = NAN;
float accelZ = NAN;

float gyroX = NAN;
float gyroY = NAN;
float gyroZ = NAN;

int gasADC = 0;
float gasVoltage = 0.0;

bool emergencyPressed = false;

unsigned long lastDHTRead = 0;
unsigned long lastOutput = 0;

const unsigned long DHT_INTERVAL_MS = 2000;
const unsigned long OUTPUT_INTERVAL_MS = 500;

// =====================================================
// MPU6050 DIRECT REGISTER FUNCTIONS
// =====================================================

bool writeMPURegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU_ADDRESS);
  Wire.write(reg);
  Wire.write(value);

  return Wire.endTransmission() == 0;
}

bool readMPURegisters(uint8_t startReg,
                      uint8_t *buffer,
                      uint8_t length) {
  Wire.beginTransmission(MPU_ADDRESS);
  Wire.write(startReg);

  if (Wire.endTransmission(false) != 0) {
    return false;
  }

  uint8_t received =
      Wire.requestFrom(MPU_ADDRESS, length, true);

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

int16_t combineBytes(uint8_t highByte, uint8_t lowByte) {
  return (int16_t)((highByte << 8) | lowByte);
}

bool initializeMPU() {
  // Wake the device.
  bool wakeOK = writeMPURegister(0x6B, 0x00);

  delay(100);

  // Accelerometer range: +/- 2 g.
  bool accelOK = writeMPURegister(0x1C, 0x00);

  // Gyroscope range: +/- 250 degrees/second.
  bool gyroOK = writeMPURegister(0x1B, 0x00);

  // Confirm that the device responds to register reads.
  uint8_t identity = 0;
  bool readOK = readMPURegisters(0x75, &identity, 1);

  Serial.print("MPU register WHO_AM_I: 0x");
  Serial.println(identity, HEX);

  // Some compatible modules report a nonstandard identity.
  // We check register communication, not a specific identity.
  return wakeOK && accelOK && gyroOK && readOK;
}

bool readMPU() {
  uint8_t data[14];

  if (!readMPURegisters(0x3B, data, 14)) {
    return false;
  }

  int16_t axRaw = combineBytes(data[0], data[1]);
  int16_t ayRaw = combineBytes(data[2], data[3]);
  int16_t azRaw = combineBytes(data[4], data[5]);

  int16_t gxRaw = combineBytes(data[8], data[9]);
  int16_t gyRaw = combineBytes(data[10], data[11]);
  int16_t gzRaw = combineBytes(data[12], data[13]);

  // Accelerometer: +/- 2 g, 16384 LSB/g.
  // Convert g to m/s^2.
  accelX = (axRaw / 16384.0f) * 9.80665f;
  accelY = (ayRaw / 16384.0f) * 9.80665f;
  accelZ = (azRaw / 16384.0f) * 9.80665f;

  // Gyroscope: +/- 250 degrees/second, 131 LSB/(degree/s).
  // Convert degrees/second to radians/second.
  const float DEG_TO_RAD_VALUE = 0.01745329252f;

  gyroX = (gxRaw / 131.0f) * DEG_TO_RAD_VALUE;
  gyroY = (gyRaw / 131.0f) * DEG_TO_RAD_VALUE;
  gyroZ = (gzRaw / 131.0f) * DEG_TO_RAD_VALUE;

  return true;
}

// =====================================================
// DHT22
// =====================================================

void readDHT() {
  float newHumidity = dht.readHumidity();
  float newTemperature = dht.readTemperature();

  if (!isnan(newTemperature) && !isnan(newHumidity)) {
    temperatureC = newTemperature;
    humidityRH = newHumidity;
  }
}

// =====================================================
// MQ-135 ANALOG INPUT
// =====================================================

void readGasSensor() {
  gasADC = analogRead(MQ135_PIN);

  // Approximate ADC-scaled voltage, not a calibrated
  // measurement of the actual sensor's gas concentration.
  gasVoltage = (gasADC / 4095.0f) * 3.3f;
}

// =====================================================
// EMERGENCY BUTTON
// =====================================================

void readEmergencyButton() {
  // INPUT_PULLUP: pressed = LOW.
  emergencyPressed =
      (digitalRead(EMERGENCY_PIN) == LOW);
}

// =====================================================
// JSON OUTPUT
// =====================================================

void printJSON() {
  Serial.print("{");

  Serial.print("\"uptime_ms\":");
  Serial.print(millis());

  Serial.print(",\"dht_status\":\"");
  Serial.print(
      (!isnan(temperatureC) && !isnan(humidityRH))
          ? "ok" : "error");
  Serial.print("\"");

  Serial.print(",\"mpu_status\":\"");
  Serial.print(mpuReady ? "ok" : "error");
  Serial.print("\"");

  Serial.print(",\"temperature\":");
  if (isnan(temperatureC)) {
    Serial.print("null");
  } else {
    Serial.print(temperatureC, 1);
  }

  Serial.print(",\"humidity\":");
  if (isnan(humidityRH)) {
    Serial.print("null");
  } else {
    Serial.print(humidityRH, 1);
  }

  Serial.print(",\"accel\":{");
  Serial.print("\"x\":");
  Serial.print(accelX, 3);
  Serial.print(",\"y\":");
  Serial.print(accelY, 3);
  Serial.print(",\"z\":");
  Serial.print(accelZ, 3);
  Serial.print("}");

  Serial.print(",\"gyro\":{");
  Serial.print("\"x\":");
  Serial.print(gyroX, 4);
  Serial.print(",\"y\":");
  Serial.print(gyroY, 4);
  Serial.print(",\"z\":");
  Serial.print(gyroZ, 4);
  Serial.print("}");

  Serial.print(",\"gas_adc\":");
  Serial.print(gasADC);

  Serial.print(",\"gas_voltage_est\":");
  Serial.print(gasVoltage, 3);

  Serial.print(",\"emergency_button\":\"");
  Serial.print(emergencyPressed ? "PRESSED" : "RELEASED");
  Serial.print("\"");

  Serial.println("}");
}

// =====================================================
// SETUP
// =====================================================

void setup() {
  Serial.begin(115200);

  pinMode(EMERGENCY_PIN, INPUT_PULLUP);

  // Keep the reserved output LOW. No buzzer is driven.
  pinMode(BUZZER_CONTROL_PIN, OUTPUT);
  digitalWrite(BUZZER_CONTROL_PIN, LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(MQ135_PIN, ADC_11db);

  Wire.begin(MPU_SDA, MPU_SCL);
  Wire.setClock(100000);

  dht.begin();

  delay(200);

  mpuReady = initializeMPU();

  // First DHT reading.
  readDHT();
  lastDHTRead = millis();

  Serial.println("SENTINEL-X combined firmware started");
}

// =====================================================
// MAIN LOOP
// =====================================================

void loop() {
  unsigned long now = millis();

  readEmergencyButton();
  readGasSensor();

  if (now - lastDHTRead >= DHT_INTERVAL_MS) {
    readDHT();
    lastDHTRead = now;
  }

  mpuReady = readMPU();

  if (now - lastOutput >= OUTPUT_INTERVAL_MS) {
    printJSON();
    lastOutput = now;
  }

  delay(10);
}