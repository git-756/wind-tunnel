#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <Servo.h>
#include <SPI.h>

// ★ リレー制御用ピン定義 (デジタル 2番, 3番ピン)
const int RELAY1_PIN     = 2;  
const int RELAY2_PIN     = 3;  

const int PIN_AD_CS      = 10;
const int PIN_AD_SDI     = 11; // MOSI
const int PIN_AD_DOUTA   = 12; // MISO
const int PIN_AD_SCLK    = 13; // SCK
const int PIN_AD_CONVST  = 9;  
const int PIN_AD_RESET   = 8;
const int PIN_AD_BUSY    = 7;  
const int SERVO_PIN      = 6;  

Adafruit_BME280 bme;
Servo myservo;

// レベル変換IC（FXMA108）を通しても波形が崩れないよう、通信速度を 1MHz に設定
SPISettings settingsAD7606(1000000, MSBFIRST, SPI_MODE2);

// --- パラメータ設定 ---
const float WIND_MAX_VOLTAGE = 1.0;
const float WIND_MAX_SPEED   = 30.0;
const float POT_PHYSICAL_MAX_DEGREES = 3606.0; 
const float POT_MEASURED_MAX_VOLTAGE = 4.951;  
const float SERVO_SPEED_DEG_PER_SEC = 15.0; 

const unsigned long SEND_INTERVAL = 50;   
const unsigned long BME_INTERVAL  = 1000; 
const unsigned long ADC_INTERVAL  = 1;    // AD7606のサンプリング間隔 1ms (1kHz)

unsigned long lastSendTime = 0;
unsigned long lastBmeTime = 0;
unsigned long lastAdcTime = 0;

double forceSum[6] = {0.0};
double windSum = 0.0;
double potSum = 0.0;
int sampleCount = 0;

float currentTemp = NAN;
float currentHum = NAN;
float currentPres = NAN;

float currentPulse = 1472.0; 
float targetPulse = 1472.0;
unsigned long lastServoUpdate = 0;

// I2Cバスのハングアップを強制リセットする関数
void resetI2CBus() {
  Wire.end();
  pinMode(SDA, OUTPUT);
  pinMode(SCL, OUTPUT);
  digitalWrite(SDA, HIGH);
  for (int i = 0; i < 9; i++) {
    digitalWrite(SCL, HIGH); delayMicroseconds(5);
    digitalWrite(SCL, LOW);  delayMicroseconds(5);
  }
  digitalWrite(SCL, HIGH);
  Wire.begin();
}

void writeRegister(uint8_t address, uint8_t data) {
  uint16_t command = ((uint16_t)(address & 0x3F) << 8) | (data & 0xFF);
  SPI.beginTransaction(settingsAD7606);
  digitalWrite(PIN_AD_CS, LOW);
  delayMicroseconds(2); 
  SPI.transfer16(command);
  delayMicroseconds(2);
  digitalWrite(PIN_AD_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(10);
}

uint8_t readRegister(uint8_t address) {
  uint16_t command = (1 << 14) | ((uint16_t)(address & 0x3F) << 8);
  SPI.beginTransaction(settingsAD7606);
  
  digitalWrite(PIN_AD_CS, LOW);
  delayMicroseconds(2);
  SPI.transfer16(command);
  delayMicroseconds(2);
  digitalWrite(PIN_AD_CS, HIGH);
  
  delayMicroseconds(5); 
  
  digitalWrite(PIN_AD_CS, LOW);
  delayMicroseconds(2);
  uint16_t response = SPI.transfer16(0x0000);
  delayMicroseconds(2);
  digitalWrite(PIN_AD_CS, HIGH);
  
  SPI.endTransaction();
  return (uint8_t)(response & 0xFF);
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(10); // シリアルタイムアウトを10msに短縮
  while (!Serial);

  delay(1000); 

  // ★ リレー用ピンを出力モードに設定＆初期状態OFF(LOW)
  pinMode(RELAY1_PIN, OUTPUT);
  pinMode(RELAY2_PIN, OUTPUT);
  digitalWrite(RELAY1_PIN, LOW);
  digitalWrite(RELAY2_PIN, LOW);

  pinMode(PIN_AD_CS, OUTPUT);
  pinMode(PIN_AD_CONVST, OUTPUT);
  pinMode(PIN_AD_RESET, OUTPUT);
  pinMode(PIN_AD_BUSY, INPUT);

  digitalWrite(PIN_AD_CS, HIGH);
  digitalWrite(PIN_AD_CONVST, LOW);
  digitalWrite(PIN_AD_RESET, LOW); 

  SPI.begin();
  delay(50);
  
  digitalWrite(PIN_AD_RESET, HIGH);
  delay(2);
  digitalWrite(PIN_AD_RESET, LOW);
  delay(20); 
  
  Serial.println("\n--- Initializing AD7606 ---");
  bool isConfigured = false;
  uint8_t checkVal = 0;
  
  for (int i = 1; i <= 5; i++) {
    writeRegister(0x03, 0x11); 
    writeRegister(0x04, 0x11); 
    writeRegister(0x05, 0x11); 
    writeRegister(0x06, 0x11); 
    writeRegister(0x00, 0x00); 
    delay(20); 
    checkVal = readRegister(0x03);
    
    if (checkVal == 0x11) {
      isConfigured = true;
      Serial.println("[OK] Range successfully set to +/- 5V (0x11).");
      break;
    } else {
      Serial.print("[Retry "); Serial.print(i); 
      Serial.print("] Failed. Read Value: 0x"); Serial.println(checkVal, HEX);
      delay(500);
    }
  }

  if (!isConfigured) {
    Serial.println("[WARNING] Could not verify 5V mode!");
  }
  Serial.println("---------------------------\n");
  delay(1000); 

  // BME280の初期化
  if (!bme.begin(0x76)) {
    Serial.println("Error: BME280 not found");
  } else {
    currentTemp = bme.readTemperature();
    currentHum = bme.readHumidity();
    currentPres = bme.readPressure() / 100.0F;
  }

  myservo.attach(SERVO_PIN);
  myservo.writeMicroseconds((int)currentPulse);

  Serial.println("Temp,Hum,Pres,CH1(V),CH2(V),CH3(V),CH4(V),CH5(V),CH6(V),PotDegrees,AvgWind");
}

void loop() {
  unsigned long currentTime = millis();

  // ★ 1. シリアルコマンド受信（サーボ角度設定 / リレー制御）
  if (Serial.available() > 0) {
    String inputStr = Serial.readStringUntil('\n');
    inputStr.trim();

    if (inputStr.equals("R1_ON")) {
      digitalWrite(RELAY1_PIN, HIGH);
    } else if (inputStr.equals("R1_OFF")) {
      digitalWrite(RELAY1_PIN, LOW);
    } else if (inputStr.equals("R2_ON")) {
      digitalWrite(RELAY2_PIN, HIGH);
    } else if (inputStr.equals("R2_OFF")) {
      digitalWrite(RELAY2_PIN, LOW);
    } else if (inputStr.length() > 0) {
      float angle = inputStr.toFloat();
      if (angle >= 0.0 && angle <= 180.0) {
        targetPulse = 544.0 + (angle / 180.0) * (2400.0 - 544.0);
      }
    }
  }

  // 2. サーボの滑らかな移動処理
  if (currentTime - lastServoUpdate >= 20) {
    float maxStep = (SERVO_SPEED_DEG_PER_SEC * (2400.0 - 544.0) / 180.0) * (20.0 / 1000.0);
    if (abs(targetPulse - currentPulse) <= maxStep) {
      currentPulse = targetPulse; 
    } else if (targetPulse > currentPulse) {
      currentPulse += maxStep;
    } else {
      currentPulse -= maxStep;
    }
    myservo.writeMicroseconds((int)currentPulse);
    lastServoUpdate = currentTime;
  }

  // 3. BME280の取得とエラーガード
  if (currentTime - lastBmeTime >= BME_INTERVAL) {
    float t = bme.readTemperature();
    float h = bme.readHumidity();
    float p = bme.readPressure() / 100.0F;

    bool isError = isnan(t) || isnan(h) || isnan(p) ||
                   (t < -40.0 || t > 80.0) ||
                   (h < 0.0   || h > 100.0) ||
                   (p < 300.0 || p > 1200.0);

    if (isError) {
      resetI2CBus();
      bme.begin(0x76); 
    } else {
      currentTemp = t;
      currentHum = h;
      currentPres = p;
    }
    lastBmeTime = currentTime;
  }

  // 4. AD7606 サンプリング（1msインターバル制限）
  if (currentTime - lastAdcTime >= ADC_INTERVAL) {
    int16_t adRaw[8];
    
    // 変換開始パルス (CONVST)
    digitalWrite(PIN_AD_CONVST, HIGH);
    delayMicroseconds(1);
    digitalWrite(PIN_AD_CONVST, LOW);
    
    // 変換完了待ち (BUSY)
    while (digitalRead(PIN_AD_BUSY) == HIGH);

    // ADCデータの読み出し
    SPI.beginTransaction(settingsAD7606);
    digitalWrite(PIN_AD_CS, LOW);
    for (int i = 0; i < 8; i++) {
      adRaw[i] = SPI.transfer16(0x0000);
    }
    digitalWrite(PIN_AD_CS, HIGH);
    SPI.endTransaction();

    for (int i = 0; i < 6; i++) {
      forceSum[i] += (adRaw[i] / 32768.0) * 5.0;
    }
    potSum += (adRaw[6] / 32768.0) * 5.0;

    float wV = (adRaw[7] / 32768.0) * 5.0; 
    float wS = wV * (WIND_MAX_SPEED / WIND_MAX_VOLTAGE);
    if (wS < 0) wS = 0;
    windSum += wS;
    
    sampleCount++;
    lastAdcTime = currentTime;
  }

  // 5. データの送信 (0.05秒周期)
  if (currentTime - lastSendTime >= SEND_INTERVAL) {
    Serial.print(currentTemp, 2); Serial.print(",");
    Serial.print(currentHum, 2);  Serial.print(",");
    Serial.print(currentPres, 2); Serial.print(",");

    for (int i = 0; i < 6; i++) {
      float avgForce = (sampleCount > 0) ? (forceSum[i] / sampleCount) : 0;
      Serial.print(avgForce, 4); Serial.print(",");
      forceSum[i] = 0.0; 
    }

    float avgPotVolt = (sampleCount > 0) ? (potSum / sampleCount) : 0;
    float avgPot = (avgPotVolt / POT_MEASURED_MAX_VOLTAGE) * POT_PHYSICAL_MAX_DEGREES;
    float avgWind = (sampleCount > 0) ? (windSum / sampleCount) : 0;

    Serial.print(avgPot, 2); Serial.print(",");
    Serial.print(avgWind, 3);
    Serial.println();

    potSum = 0.0;
    windSum = 0.0;
    sampleCount = 0;
    lastSendTime = currentTime;
  }
}