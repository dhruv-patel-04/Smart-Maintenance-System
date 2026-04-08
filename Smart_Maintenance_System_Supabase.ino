#include <WiFi.h>
#include <HTTPClient.h>
#include "time.h"

#include <OneWire.h>
#include <DallasTemperature.h> 
#include "EmonLib.h"
#include <Wire.h>
#include <MPU6050.h> 
#include <math.h>

/* =========================
   WIFI
   ========================= */
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

/* =========================
   SUPABASE CONFIG
   ========================= */
String supabaseUrl = "https://YOUR_PROJECT_ID.supabase.co/rest/v1/raw_sensor_readings";
String supabaseKey = "YOUR_SUPABASE_ANON_OR_SERVICE_ROLE_JWT";

/* =========================
   TIME
   ========================= */
const char* ntpServer = "pool.ntp.org";
const long gmtOffset_sec = 19800;

/* =========================
   CONFIG
   ========================= */
// Config fetched once at startup (keeps calibration consistent)
struct Config {
  float calibration = 52;
  float turns = 2;
  float alpha = 0.02;
};
Config config;

/* =========================
   TEMPERATURE (DS18B20)
   ========================= */
OneWire oneWire(4);
DallasTemperature tempSensor(&oneWire);
float temperatureC = 0;

/* =========================
   CURRENT (SCT-013)
   ========================= */
EnergyMonitor emon1;
double currentRMS = 0;

/* =========================
   VIBRATION (MPU6050)
   ========================= */
MPU6050 mpu;
#define ACC_SENS 2048.0   // ±16G scaling

float meanX = 0, meanY = 0, meanZ = 0;   // gravity estimate
float vibSumSquares = 0;                 // accumulation for RMS
int vibSampleCount = 0;
float vibrationRMS = 0;

/* =========================
   TIMING
   ========================= */
unsigned long lastReportTime = 0;
unsigned long lastTempRequest = 0;
unsigned long lastVibSample = 0;
unsigned long lastUpload = 0;

const unsigned long REPORT_INTERVAL = 1000;   // 1 sec processing
const unsigned long TEMP_INTERVAL = 1000;     // 1 sec temp update
const unsigned long VIB_INTERVAL = 5;         // 200 Hz sampling (IMPORTANT)
const unsigned long UPLOAD_INTERVAL = 5000;   // 5 sec upload
unsigned long systemStartTime = 0;            // to handle initial garbage value of current sensor

/* =========================
   SETUP
   ========================= */
void setup() {
  Serial.begin(115200);
  delay(1000);

  /* -------- WiFi -------- */
  WiFi.begin(ssid, password);
  Serial.print("Connecting WiFi");
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected!");

  /* -------- NTP Time -------- */
  configTime(gmtOffset_sec, 0, ntpServer);

  // Wait until time is synced (important for correct timestamp)
  struct tm timeinfo;
  while (!getLocalTime(&timeinfo)) {
    Serial.println("Waiting for NTP...");
    delay(500);
  }

  /* -------- Fetch Config -------- */
  fetchConfig();

  /* -------- Temperature Setup -------- */
  tempSensor.begin();
  tempSensor.setResolution(10);
  tempSensor.setWaitForConversion(false);
  tempSensor.requestTemperatures();
  lastTempRequest = millis();

  /* -------- Current Sensor Setup -------- */
  emon1.current(34, config.calibration);

  /* -------- MPU6050 Setup -------- */
  Wire.begin(21, 22);
  Wire.setClock(400000);

  Wire.beginTransmission(0x68);
  Wire.write(0x6B);
  Wire.write(0x00);
  Wire.endTransmission(true);

  delay(100);

  mpu.initialize();
  mpu.setFullScaleAccelRange(MPU6050_ACCEL_FS_16);
  Serial.println("System Ready");

  //to handle garbage current value
  systemStartTime = millis();          
}

/* =========================
   LOOP
   ========================= */
void loop() {

  unsigned long now = millis();

  /* =========================
     1️. VIBRATION SAMPLING (200 Hz)
     =========================
     MUST remain EXACTLY SAME as dataset logic
  */
  if (now - lastVibSample >= VIB_INTERVAL) {
    lastVibSample = now;

    int16_t x, y, z;
    mpu.getAcceleration(&x, &y, &z);

    float ax = x / ACC_SENS;
    float ay = y / ACC_SENS;
    float az = z / ACC_SENS;

    // Low-pass filter → estimate gravity
    meanX = (1 - config.alpha) * meanX + config.alpha * ax;
    meanY = (1 - config.alpha) * meanY + config.alpha * ay;
    meanZ = (1 - config.alpha) * meanZ + config.alpha * az;

    // Remove gravity → dynamic acceleration
    float dx = ax - meanX;
    float dy = ay - meanY;
    float dz = az - meanZ;

    float magnitude = sqrt(dx*dx + dy*dy + dz*dz);

    // Accumulate for RMS (IMPORTANT)
    vibSumSquares += magnitude * magnitude;
    vibSampleCount++;
  }

  /* =========================
     2️. TEMPERATURE UPDATE
     ========================= */
  if (now - lastTempRequest >= TEMP_INTERVAL) {
    temperatureC = tempSensor.getTempCByIndex(0);
    tempSensor.requestTemperatures();
    lastTempRequest = now;
  }

  /* =========================
     3️. 1-SECOND PROCESSING
     =========================
     THIS DEFINES YOUR DATASET FEATURE
     -> MUST match previous code
  */
  if (now - lastReportTime >= REPORT_INTERVAL) {
    lastReportTime = now;

    // Current RMS (already correct)
    currentRMS = emon1.calcIrms(1480);
    currentRMS = currentRMS / config.turns;
    if (currentRMS < 0.05) currentRMS = 0;

    // Vibration RMS over 1-second window
    if (vibSampleCount > 0) {
      vibrationRMS = sqrt(vibSumSquares / vibSampleCount);
    }

    // VERY IMPORTANT: reset AFTER 1 second
    vibSumSquares = 0;
    vibSampleCount = 0;
  }

  /* =========================
     4️. SUPABASE UPLOAD (5 sec)
     =========================
     Sends latest 1-sec computed values
  */
  if ((now - lastUpload >= UPLOAD_INTERVAL) && (now - systemStartTime > 10000)) {
    lastUpload = now;

    sendToSupabase(temperatureC, currentRMS, vibrationRMS);
  }
}

/* =========================
   SUPABASE SEND
   ========================= */
void sendToSupabase(float temp, float current, float vibration) {

  HTTPClient http;
   String loadLevel = "High";

   if (current < 4) {
      loadLevel = "Low";
   } else if (current < 7) {
      loadLevel = "Medium";
   }

  struct tm timeinfo;
  char timeString[25];

  if (getLocalTime(&timeinfo)) {
    strftime(timeString, sizeof(timeString), "%Y-%m-%d %H:%M:%S", &timeinfo);
  }

  // JSON for Supabase (column names must match table)
  String json = "[{";
   json += "\"machine_id\":\"motor_1\",";
   json += "\"event_ts\":\"" + String(timeString) + "\",";
   json += "\"temp_c\":" + String(temp,2) + ",";
   json += "\"current_a\":" + String(current,3) + ",";
   json += "\"vibration_rms_g\":" + String(vibration,6) + ",";
   json += "\"load_level\":\"" + loadLevel + "\",";
   json += "\"source\":\"esp32\"";
  json += "}]";

  http.begin(supabaseUrl);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("apikey", supabaseKey);
  http.addHeader("Authorization", "Bearer " + supabaseKey);
   http.addHeader("Content-Profile", "iot");
   http.addHeader("Accept-Profile", "iot");
  http.addHeader("Prefer", "return=minimal");

  int httpResponseCode = http.POST(json);

  Serial.print("Supabase Response: ");
  Serial.println(httpResponseCode);

  http.end();
}

/* =========================
   CONFIG FETCH
   ========================= */
void fetchConfig() {

  HTTPClient http;

   String url = "https://YOUR_PROJECT_ID.supabase.co/rest/v1/device_config?device_id=eq.motor_1";

  http.begin(url);
  http.addHeader("apikey", supabaseKey);
  http.addHeader("Authorization", "Bearer " + supabaseKey);

  int code = http.GET();

  if (code == 200) {
    String payload = http.getString();

    // Example response:
    // [{"device_id":"motor_1","calibration":52,"turns":2,"alpha":0.02}]

    config.calibration = getValue(payload, "calibration").toFloat();
    config.turns = getValue(payload, "turns").toFloat();
    config.alpha = getValue(payload, "alpha").toFloat();

    Serial.println("Config Loaded from Supabase");
  } else {
    Serial.println("Using Default Config");
  }

  http.end();
}

/* =========================
   SIMPLE JSON PARSER
   ========================= */
String getValue(String data, String key) {
  int start = data.indexOf(key);
  int colon = data.indexOf(":", start);
  int comma = data.indexOf(",", colon);

  if (comma == -1) comma = data.indexOf("}", colon);

  return data.substring(colon + 1, comma);
}