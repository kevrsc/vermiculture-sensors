#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <esp_system.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <esp32-hal-bt.h>
#include <esp_bt.h>
#include "secrets.h"

#define ONE_WIRE_BUS 4
#define POST_INTERVAL_MS 15000
#define KEEPALIVE_INTERVAL_MS (30UL * 60UL * 1000UL)
#define KEEPALIVE_PULSE_MS 3000UL

// Set this to true after the capacitive moisture probes are wired.
const bool MOISTURE_ENABLED = false;

// Capacitive soil-moisture probes. Wi-Fi uses ADC2, so these must be ADC1
// pins: 32, 33, 34, 35, 36, 39. GPIO 4 stays the temperature bus.
// This first probe is GPIO 32. Append 33, 34, and 35 for the other three.
// const int MOISTURE_PINS[] = {32, 33, 34, 35};
const int MOISTURE_PINS[] = {35};
const int MOISTURE_COUNT = sizeof(MOISTURE_PINS) / sizeof(MOISTURE_PINS[0]);

// Replace these with the raw values printed while the probe is in air (dry)
// and in water (wet). Keep the circuit board above the water line.
// A higher raw value is drier. Percent is 0 at the dry value and 100 at the wet value.
const int MOISTURE_DRY_RAW = 3490; // 2700;
const int MOISTURE_WET_RAW = 1565; // 1200;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
WiFiClient wifiClient;
Preferences bootPrefs;

uint32_t bootCount = 0;
uint32_t postFailures = 0;
uint32_t postSeq = 0;
uint32_t wifiReconnects = 0;
bool wifiWasConnected = false;
const char *keepaliveResult = "pending";
unsigned long lastKeepaliveMs = 0;

const char *resetReasonName() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "POWERON";
    case ESP_RST_EXT: return "EXT";
    case ESP_RST_SW: return "SW";
    case ESP_RST_PANIC: return "PANIC";
    case ESP_RST_INT_WDT: return "INT_WDT";
    case ESP_RST_TASK_WDT: return "TASK_WDT";
    case ESP_RST_WDT: return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT: return "BROWNOUT";
    case ESP_RST_SDIO: return "SDIO";
    default: return "UNKNOWN";
  }
}

void rememberBoot() {
  bootPrefs.begin("vsensors", false);
  bootCount = bootPrefs.getUInt("boots", 0) + 1;
  bootPrefs.putUInt("boots", bootCount);
  bootPrefs.end();
}

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiWasConnected = true;
    return;
  }

  if (wifiWasConnected) {
    wifiReconnects++;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to ");
  Serial.print(WIFI_SSID);
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 20000) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    wifiWasConnected = true;
    Serial.print("Wi-Fi connected, board IP ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Wi-Fi not connected, will retry");
  }
}

String sensorAddress(DeviceAddress address) {
  String text;
  for (uint8_t i = 0; i < 8; i++) {
    if (address[i] < 16) {
      text += "0";
    }
    text += String(address[i], HEX);
  }
  return text;
}

int readMoistureRaw(int pin) {
  analogRead(pin);
  long sum = 0;
  const int samples = 16;
  for (int i = 0; i < samples; i++) {
    sum += analogRead(pin);
    delay(2);
  }
  return (int)(sum / samples);
}

int moisturePercent(int raw) {
  int span = MOISTURE_DRY_RAW - MOISTURE_WET_RAW;
  if (span == 0) {
    return 0;
  }
  long percent = (long)(MOISTURE_DRY_RAW - raw) * 100L / span;
  if (percent < 0) {
    return 0;
  }
  if (percent > 100) {
    return 100;
  }
  return (int)percent;
}

String statusJson(int probeCount) {
  String status = "\"status\":{";
  status += "\"uptime_ms\":";
  status += millis();
  status += ",\"reset_reason\":\"";
  status += resetReasonName();
  status += "\",\"boot_count\":";
  status += bootCount;
  status += ",\"rssi_dbm\":";
  if (WiFi.status() == WL_CONNECTED) {
    status += WiFi.RSSI();
  } else {
    status += "null";
  }
  status += ",\"ip\":";
  if (WiFi.status() == WL_CONNECTED) {
    status += "\"";
    status += WiFi.localIP().toString();
    status += "\"";
  } else {
    status += "null";
  }
  status += ",\"wifi_reconnects\":";
  status += wifiReconnects;
  status += ",\"post_failures\":";
  status += postFailures;
  status += ",\"seq\":";
  status += postSeq;
  status += ",\"free_heap\":";
  status += ESP.getFreeHeap();
  status += ",\"min_free_heap\":";
  status += ESP.getMinFreeHeap();
  status += ",\"sketch_md5\":\"";
  status += ESP.getSketchMD5();
  status += "\",\"keepalive\":\"";
  status += keepaliveResult;
  status += "\",\"keepalive_age_ms\":";
  if (lastKeepaliveMs == 0) {
    status += "null";
  } else {
    status += millis() - lastKeepaliveMs;
  }
  status += ",\"probe_count\":";
  status += probeCount;
  status += "}";
  return status;
}

String readingJson() {
  sensors.requestTemperatures();
  int count = sensors.getDeviceCount();
  String body = "{\"device\":\"vermiculture-esp32\",\"sensors\":[";

  for (int i = 0; i < count; i++) {
    if (i > 0) {
      body += ",";
    }
    DeviceAddress address;
    sensors.getAddress(address, i);
    float celsius = sensors.getTempC(address);
    body += "{\"index\":";
    body += i;
    body += ",\"address\":\"";
    body += sensorAddress(address);
    body += "\",\"celsius\":";
    if (celsius == DEVICE_DISCONNECTED_C) {
      body += "null";
    } else {
      body += String(celsius, 2);
    }
    body += "}";
  }

  body += "],\"moisture\":[";
  if (MOISTURE_ENABLED) {
    for (int i = 0; i < MOISTURE_COUNT; i++) {
      if (i > 0) {
        body += ",";
      }
      int raw = readMoistureRaw(MOISTURE_PINS[i]);
      body += "{\"index\":";
      body += i;
      body += ",\"pin\":";
      body += MOISTURE_PINS[i];
      body += ",\"raw\":";
      body += raw;
      body += ",\"percent\":";
      body += moisturePercent(raw);
      body += "}";
    }
  }

  body += "],";
  body += statusJson(count);
  body += "}";
  return body;
}

bool postReading(const String &body) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  HTTPClient http;
  http.setTimeout(5000);
  if (!http.begin(wifiClient, SERVER_URL)) {
    Serial.println("Could not start HTTP client");
    return false;
  }

  http.addHeader("Content-Type", "application/json");
  int status = http.POST(body);
  Serial.print("POST ");
  Serial.print(SERVER_URL);
  Serial.print(" -> ");
  Serial.println(status);
  http.end();
  return status >= 200 && status < 300;
}

void bluetoothKeepalive() {
  unsigned long now = millis();
  if (lastKeepaliveMs != 0 && now - lastKeepaliveMs < KEEPALIVE_INTERVAL_MS) {
    return;
  }
  lastKeepaliveMs = now;

  Serial.println("Bluetooth keepalive pulse");
  if (!btStartMode(BT_MODE_BLE)) {
    keepaliveResult = "failed";
    Serial.println("Bluetooth keepalive failed");
    return;
  }
  esp_bt_sleep_disable();
  delay(KEEPALIVE_PULSE_MS);
  btStop();
  keepaliveResult = "ok";
  Serial.println("Bluetooth keepalive done");
}

void setup() {
  Serial.begin(115200);
  delay(500);
  rememberBoot();
  sensors.begin();
  if (MOISTURE_ENABLED) {
    analogReadResolution(12);
    Serial.print("Moisture inputs:");
    for (int i = 0; i < MOISTURE_COUNT; i++) {
      analogSetPinAttenuation(MOISTURE_PINS[i], ADC_11db);
      Serial.print(" GPIO ");
      Serial.print(MOISTURE_PINS[i]);
    }
    Serial.println();
  }

  if (strlen(WIFI_PASSWORD) == 0) {
    Serial.println("Set WIFI_PASSWORD in secrets.h, then upload again.");
  }

  Serial.print("Found ");
  Serial.print(sensors.getDeviceCount());
  Serial.println(" temperature probes");
  connectWifi();
}

void loop() {
  if (strlen(WIFI_PASSWORD) == 0) {
    delay(5000);
    return;
  }

  connectWifi();
  bluetoothKeepalive();
  postSeq++;
  String body = readingJson();
  Serial.println(body);
  if (!postReading(body)) {
    postFailures++;
    Serial.println("Post failed");
  }
  delay(POST_INTERVAL_MS);
}
