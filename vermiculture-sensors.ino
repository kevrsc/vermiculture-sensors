#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <esp_system.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <esp32-hal-bt.h>
#include <esp_bt.h>
#include <math.h>
#include "secrets.h"
#include "sensor_server.h"

#define ONE_WIRE_BUS 4
#define POST_INTERVAL_MS (15UL * 60UL * 1000UL)
#define KEEPALIVE_INTERVAL_MS (60UL * 60UL * 1000UL)
#define KEEPALIVE_PULSE_MS 10000UL

// Set this to true to pulse Bluetooth on KEEPALIVE_INTERVAL_MS.
const bool BLUETOOTH_KEEPALIVE_ENABLED = false;

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
  status += BLUETOOTH_KEEPALIVE_ENABLED ? keepaliveResult : "disabled";
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

int16_t centiCelsius(float celsius) {
  if (celsius == DEVICE_DISCONNECTED_C) {
    return INT16_MIN;
  }
  float scaled = celsius * 100.0f;
  if (scaled > 32766.0f || scaled < -32767.0f) {
    return INT16_MIN;
  }
  return (int16_t)lroundf(scaled);
}

String readingJson(ReadingSample &sample) {
  sensorBlankSample(sample);
  sensors.requestTemperatures();
  int count = sensors.getDeviceCount();
  String body = "{\"device\":\"vermiculture-esp32\",\"sensors\":[";

  int kept = count;
  if (kept > SENSOR_SERVER_MAX_PROBES) {
    kept = SENSOR_SERVER_MAX_PROBES;
  }
  sample.probeCount = kept;

  for (int i = 0; i < count; i++) {
    if (i > 0) {
      body += ",";
    }
    DeviceAddress address;
    sensors.getAddress(address, i);
    float celsius = sensors.getTempC(address);
    int16_t centi = centiCelsius(celsius);
    if (i < SENSOR_SERVER_MAX_PROBES) {
      memcpy(sample.probes[i].address, address, 8);
      sample.probes[i].celsiusX100 = centi;
    }
    body += "{\"index\":";
    body += i;
    body += ",\"address\":\"";
    body += sensorAddress(address);
    body += "\",\"celsius\":";
    char temp[16];
    if (sensorFormatCenti(centi, temp, sizeof(temp))) {
      body += temp;
    } else {
      body += "null";
    }
    body += "}";
  }

  body += "],";
  body += statusJson(count);
  if (sensorClockReady()) {
    uint32_t now = (uint32_t)time(nullptr);
    char stamp[32];
    if (sensorFormatTime(now, stamp, sizeof(stamp))) {
      sample.unixTime = now;
      body += ",\"received_at\":\"";
      body += stamp;
      body += "\"";
    }
  }
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
  if (!BLUETOOTH_KEEPALIVE_ENABLED) {
    return;
  }

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
  if (strlen(WIFI_PASSWORD) == 0) {
    Serial.println("Set WIFI_PASSWORD in secrets.h, then upload again.");
  }

  Serial.print("Found ");
  Serial.print(sensors.getDeviceCount());
  Serial.println(" temperature probes");
  connectWifi();
  sensorServerBegin();
  sensorServerSync();
}

void loop() {
  if (strlen(WIFI_PASSWORD) == 0) {
    delay(5000);
    return;
  }

  connectWifi();
  sensorServerLoop();

  static unsigned long lastPostMs = 0;
  if (lastPostMs != 0 && millis() - lastPostMs < POST_INTERVAL_MS) {
    delay(10);
    return;
  }
  lastPostMs = millis();

  bluetoothKeepalive();
  postSeq++;
  ReadingSample sample;
  String body = readingJson(sample);
  sensorServerSetLatest(body);
  if (sample.unixTime != 0 && !sensorServerRecord(sample)) {
    Serial.println("Reading was not stored on the board");
  }
  Serial.println(body);
  if (!postReading(body)) {
    postFailures++;
    Serial.println("Post failed");
  }
}
