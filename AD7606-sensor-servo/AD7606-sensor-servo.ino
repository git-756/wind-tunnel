#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <Servo.h>
#include <SPI.h>

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

unsigned long lastSendTime = 0;
unsigned long lastBmeTime = 0;

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

// ==========================================
// AD7606レジスタ書き込み関数（バグ修正版）
// ==========================================
void writeRegister(uint8_t address, uint8_t data) {
  // AD7606CのSPIレジスタ書き込みフォーマット:
  // [15] アドレス書き込み有効 (常に0)
  // [14] W/Rビット (Writeは0)
  // [13:8] アドレス (6ビット) -> 8ビット左シフト
  // [7:0] データ (8ビット)
  uint16_t command = ((uint16_t)(address & 0x3F) << 8) | (data & 0xFF);
  
  SPI.beginTransaction(settingsAD7606);
  digitalWrite(PIN_AD_CS, LOW);
  delayMicroseconds(2); // レベル変換ICの応答のための僅かなウェイト
  SPI.transfer16(command);
  delayMicroseconds(2);
  digitalWrite(PIN_AD_CS, HIGH);
  SPI.endTransaction();
  
  delayMicroseconds(10);
}

// ==========================================
// AD7606レジスタ読み出し関数（バグ修正版）
// ==========================================
uint8_t readRegister(uint8_t address) {
  // AD7606CのSPIレジスタ読み出しフォーマット:
  // [15] アドレス書き込み有効 (常に0)
  // [14] W/Rビット (Readは1)
  // [13:8] アドレス (6ビット) -> 8ビット左シフト
  // [7:0] ダミー (常に0)
  uint16_t command = (1 << 14) | ((uint16_t)(address & 0x3F) << 8);
  
  SPI.beginTransaction(settingsAD7606);
  
  // 1. コマンドフレームの送信（アドレスと読み出し指令を送る）
  digitalWrite(PIN_AD_CS, LOW);
  delayMicroseconds(2);
  SPI.transfer16(command);
  delayMicroseconds(2);
  digitalWrite(PIN_AD_CS, HIGH);
  
  delayMicroseconds(5); // デバイスの準備待ち
  
  // 2. リードフレーム（ダミーを送信して、DOUTAから返ってきた値を受信）
  digitalWrite(PIN_AD_CS, LOW);
  delayMicroseconds(2);
  uint16_t response = SPI.transfer16(0x0000);
  delayMicroseconds(2);
  digitalWrite(PIN_AD_CS, HIGH);
  
  SPI.endTransaction();
  
  // 返ってきたデータのLLSB（下位8ビット）がレジスタの値
  return (uint8_t)(response & 0xFF);
}

void setup() {
  Serial.begin(115200);
  while (!Serial);

  delay(1000); // 起動安定待ち

  pinMode(PIN_AD_CS, OUTPUT);
  pinMode(PIN_AD_CONVST, OUTPUT);
  pinMode(PIN_AD_RESET, OUTPUT);
  pinMode(PIN_AD_BUSY, INPUT);

  digitalWrite(PIN_AD_CS, HIGH);
  digitalWrite(PIN_AD_CONVST, LOW);
  digitalWrite(PIN_AD_RESET, LOW); 

  SPI.begin();
  delay(50);
  
  // --- AD7606C-16 リセットシーケンス ---
  digitalWrite(PIN_AD_RESET, HIGH);
  delay(2);
  digitalWrite(PIN_AD_RESET, LOW);
  delay(20); 
  
  // ==========================================
  // ±5V設定と、SPI双方向通信による設定確認
  // ==========================================
  Serial.println("\n--- Initializing AD7606 ---");
  bool isConfigured = false;
  uint8_t checkVal = 0;
  
  // 最大5回リトライする
  for (int i = 1; i <= 5; i++) {
    // 各チャンネルのレンジを設定（0x11: すべて ±5V レンジ）
    writeRegister(0x03, 0x11); // CH1, CH2
    writeRegister(0x04, 0x11); // CH3, CH4
    writeRegister(0x05, 0x11); // CH5, CH6
    writeRegister(0x06, 0x11); // CH7, CH8
    
    // 設定を有効化するため、一時的にADC読込モード（レジスタアドレス0x00にダミー）に戻す
    writeRegister(0x00, 0x00); 
    
    delay(20); 
    
    // 設定が正しく書き込まれたか検証（CH1, CH2のレンジレジスタ 0x03 を確認）
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

  // 失敗した場合の警告
  if (!isConfigured) {
    Serial.println("[WARNING] Could not verify 5V mode! Chip might be running in +/- 10V mode.");
    Serial.println("If Read Value is 0x00 or 0xFF, the chip might be non-C AD7606 (Hardware mode only).");
    Serial.println("Please check if OS0-OS2 pins are physically tied to HIGH (Software mode trigger).");
  }
  Serial.println("---------------------------\n");
  delay(1000); // シリアルモニタ確認用

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

  // 1. サーボの目標角度受信
  if (Serial.available() > 0) {
    float angle = Serial.parseFloat();
    while (Serial.available() > 0) Serial.read();
    if (angle >= 0.0 && angle <= 180.0) {
      targetPulse = 544.0 + (angle / 180.0) * (2400.0 - 544.0);
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
      bme.begin(0x76); 
    } else {
      currentTemp = t;
      currentHum = h;
      currentPres = p;
    }
    lastBmeTime = currentTime;
  }

  // 4. AD7606 高速サンプリング
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

  // 計算は「5Vレンジ設定」の前提（1 LSB = 5.0V / 32768）
  for (int i = 0; i < 6; i++) {
    forceSum[i] += (adRaw[i] / 32768.0) * 5.0;
  }
  potSum += (adRaw[6] / 32768.0) * 5.0;

  float wV = (adRaw[7] / 32768.0) * 5.0; 
  float wS = wV * (WIND_MAX_SPEED / WIND_MAX_VOLTAGE);
  if (wS < 0) wS = 0;
  windSum += wS;
  
  sampleCount++;

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