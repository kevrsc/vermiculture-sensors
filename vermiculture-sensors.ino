#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include "secrets.h"

#define ONE_WIRE_BUS 4
#define POST_INTERVAL_MS 15000

// Capacitive soil-moisture probes. Wi-Fi uses ADC2, so these must be ADC1
// pins: 32, 33, 34, 35, 36, 39. GPIO 4 stays the temperature bus.
// This first probe is GPIO 32. Append 33, 34, and 35 for the other three.
const int MOISTURE_PINS[] = {32};
const int MOISTURE_COUNT = sizeof(MOISTURE_PINS) / sizeof(MOISTURE_PINS[0]);

// Replace these with the raw values printed while the probe is in air (dry)
// and in water (wet). Keep the circuit board above the water line.
// A higher raw value is drier. Percent is 0 at the dry value and 100 at the wet value.
const int MOISTURE_DRY_RAW = 2700;
const int MOISTURE_WET_RAW = 1200;

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);
WiFiClient wifiClient;

void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
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

  body += "]}";
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

void setup() {
  Serial.begin(115200);
  delay(500);
  sensors.begin();
  analogReadResolution(12);
  Serial.print("Moisture inputs:");
  for (int i = 0; i < MOISTURE_COUNT; i++) {
    analogSetPinAttenuation(MOISTURE_PINS[i], ADC_11db);
    Serial.print(" GPIO ");
    Serial.print(MOISTURE_PINS[i]);
  }
  Serial.println();

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
  String body = readingJson();
  Serial.println(body);
  if (!postReading(body)) {
    Serial.println("Post failed");
  }
  delay(POST_INTERVAL_MS);
}
