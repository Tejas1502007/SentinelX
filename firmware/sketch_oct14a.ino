
#include <Wire.h>
#include <DHT.h>

#define DHT_PIN 4
#define DHT_TYPE DHT22
#define MPU_ADDR 0x68

DHT dht(DHT_PIN, DHT_TYPE);

void setup() {
  Serial.begin(115200);
  delay(1000);

  dht.begin();
  Wire.begin(21, 22);
  Wire.setClock(100000);

  Serial.println("\nSENTINEL-X SENSOR TEST");

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x00);
  byte err = Wire.endTransmission();

  Serial.print("MPU wake-up status: ");
  Serial.println(err == 0 ? "OK" : "ERROR");
}

void loop() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();

  Serial.println("\n--- SENSOR DATA ---");

  if (!isnan(t) && !isnan(h)) {
    Serial.print("Temperature: ");
    Serial.print(t, 1);
    Serial.println(" C");

    Serial.print("Humidity: ");
    Serial.print(h, 1);
    Serial.println(" %RH");
  } else {
    Serial.println("DHT22 read failed");
  }

  // Read 14 bytes from MPU6050
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  byte err = Wire.endTransmission(false);

  if (err == 0) {
    byte count = Wire.requestFrom(
      (uint8_t)MPU_ADDR, (size_t)14, true
    );

    Serial.print("MPU bytes received: ");
    Serial.println(count);

    if (count == 14) {
      int16_t ax = (int16_t)((Wire.read() << 8) | Wire.read());
      int16_t ay = (int16_t)((Wire.read() << 8) | Wire.read());
      int16_t az = (int16_t)((Wire.read() << 8) | Wire.read());

      Wire.read();
      Wire.read();

      int16_t gx = (int16_t)((Wire.read() << 8) | Wire.read());
      int16_t gy = (int16_t)((Wire.read() << 8) | Wire.read());
      int16_t gz = (int16_t)((Wire.read() << 8) | Wire.read());

      Serial.println("MPU6050: OK");
      Serial.print("Accel X: "); Serial.println(ax);
      Serial.print("Accel Y: "); Serial.println(ay);
      Serial.print("Accel Z: "); Serial.println(az);
      Serial.print("Gyro X: "); Serial.println(gx);
      Serial.print("Gyro Y: "); Serial.println(gy);
      Serial.print("Gyro Z: "); Serial.println(gz);
    } else {
      while (Wire.available()) Wire.read();
      Serial.println("MPU6050: Incomplete data");
    }
  } else {
    Serial.print("MPU register error: ");
    Serial.println(err);
  }

  Serial.println("-------------------");
  delay(2000);
}
