#include "sensor_server.h"

#include <ESPmDNS.h>
#include <LittleFS.h>
#include <WebServer.h>
#include <WiFi.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "web_page_gz.h"

// History is a fixed ring in LittleFS, so the board needs a partition that
// contains a SPIFFS/LittleFS area. "Default 4MB with spiffs" holds this ring.
// 11,520 records is about 120 days at a 15-minute sample interval.
static const char *RING_PATH = "/readings.bin";
static const uint32_t RING_MAGIC = 0x31525356;
static const uint16_t RING_VERSION = 2;
static const char *TIME_ZONE = "PST8PDT,M3.2.0,M11.1.0";
static const uint32_t CLOCK_VALID_AFTER = 1700000000;

struct __attribute__((packed)) RingHeader {
  uint32_t magic;
  uint16_t version;
  uint16_t recordSize;
  uint32_t capacity;
  uint32_t next;
};

static_assert(sizeof(RingHeader) == 16, "RingHeader size changed");

static WebServer server(80);
static RingHeader ring = {};
static bool storageReady = false;
static bool clockStarted = false;
static bool mdnsStarted = false;
static bool chartPrinted = false;
static String latestJson;

static uint32_t chooseCapacity() {
  size_t total = LittleFS.totalBytes();
  size_t reserve = 64 * 1024;
  size_t budget = total > reserve ? total - reserve : total / 2;
  uint32_t fit = budget / sizeof(ReadingSample);
  const uint32_t desired = 11520;
  if (fit > desired) {
    fit = desired;
  }
  if (fit < 16) {
    fit = 16;
  }
  return fit;
}

static bool writeHeader(File &file) {
  if (!file.seek(0)) {
    return false;
  }
  return file.write(reinterpret_cast<const uint8_t *>(&ring), sizeof(ring)) == sizeof(ring);
}

static bool createRing() {
  ring.magic = RING_MAGIC;
  ring.version = RING_VERSION;
  ring.recordSize = sizeof(ReadingSample);
  ring.capacity = chooseCapacity();
  ring.next = 0;
  File file = LittleFS.open(RING_PATH, "w");
  if (!file) {
    return false;
  }
  bool wrote = writeHeader(file);
  file.close();
  return wrote;
}

static bool ringFits(const File &file) {
  if (ring.magic != RING_MAGIC || ring.version != RING_VERSION) {
    return false;
  }
  if (ring.recordSize != sizeof(ReadingSample) || ring.capacity < 16) {
    return false;
  }
  uint32_t stored = ring.next < ring.capacity ? ring.next : ring.capacity;
  size_t need = sizeof(RingHeader) + (size_t)stored * sizeof(ReadingSample);
  return file.size() >= need;
}

static void beginStorage() {
  if (!LittleFS.begin(true)) {
    Serial.println("LittleFS did not mount. Choose a partition scheme with a SPIFFS area, such as Default 4MB with spiffs.");
    return;
  }
  File file = LittleFS.open(RING_PATH, "r");
  if (!file || file.size() < sizeof(RingHeader) || file.read(reinterpret_cast<uint8_t *>(&ring), sizeof(ring)) != sizeof(ring) || !ringFits(file)) {
    if (file) {
      file.close();
    }
    if (!createRing()) {
      Serial.println("Could not create the reading ring.");
      return;
    }
    Serial.println("Started a new reading ring.");
  } else {
    file.close();
  }
  storageReady = true;
  uint32_t stored = ring.next < ring.capacity ? ring.next : ring.capacity;
  Serial.printf("History ring: %u of %u samples, %u bytes each\n", stored, ring.capacity, (unsigned)sizeof(ReadingSample));
}

static bool readSample(File &file, uint32_t slot, ReadingSample &sample) {
  size_t offset = sizeof(RingHeader) + (size_t)slot * sizeof(ReadingSample);
  if (offset + sizeof(ReadingSample) > file.size() || !file.seek(offset)) {
    return false;
  }
  return file.read(reinterpret_cast<uint8_t *>(&sample), sizeof(sample)) == sizeof(sample);
}

struct JsonBuf {
  char *buf;
  int cap;
  int used;
  bool overflow;

  void add(const char *text) {
    if (overflow || text == nullptr) {
      return;
    }
    int length = strlen(text);
    if (used + length >= cap) {
      overflow = true;
      return;
    }
    memcpy(buf + used, text, length);
    used += length;
    buf[used] = 0;
  }
};

static void addHex(JsonBuf &out, const uint8_t address[8]) {
  char hex[17];
  static const char digits[] = "0123456789abcdef";
  for (int i = 0; i < 8; i++) {
    hex[i * 2] = digits[address[i] >> 4];
    hex[i * 2 + 1] = digits[address[i] & 0x0f];
  }
  hex[16] = 0;
  out.add(hex);
}

static int formatSample(const ReadingSample &sample, char *buf, int cap) {
  char stamp[32];
  if (!sensorFormatTime(sample.unixTime, stamp, sizeof(stamp))) {
    return -1;
  }
  JsonBuf out = {buf, cap, 0, false};
  if (cap > 0) {
    buf[0] = 0;
  }
  out.add("{\"device\":\"vermiculture-esp32\",\"sensors\":[");
  int probes = sample.probeCount;
  if (probes > SENSOR_SERVER_MAX_PROBES) {
    probes = SENSOR_SERVER_MAX_PROBES;
  }
  for (int i = 0; i < probes; i++) {
    if (i > 0) {
      out.add(",");
    }
    char head[32];
    snprintf(head, sizeof(head), "{\"index\":%d,\"address\":\"", i);
    out.add(head);
    addHex(out, sample.probes[i].address);
    out.add("\",\"celsius\":");
    char temp[16];
    if (sensorFormatCenti(sample.probes[i].celsiusX100, temp, sizeof(temp))) {
      out.add(temp);
    } else {
      out.add("null");
    }
    out.add("}");
  }
  out.add("],\"received_at\":\"");
  out.add(stamp);
  out.add("\"}");
  if (out.overflow) {
    return -1;
  }
  return out.used;
}

static void sendJson(const String &body) {
  server.sendHeader("Cache-Control", "no-store");
  server.send(200, "application/json", body);
}

static void handleRoot() {
  server.sendHeader("Content-Encoding", "gzip");
  server.sendHeader("Cache-Control", "no-store");
  server.chunkResponseBegin("text/html");
  uint8_t chunk[1024];
  size_t offset = 0;
  while (offset < kIndexHtmlGzLen && server.client().connected()) {
    size_t length = kIndexHtmlGzLen - offset;
    if (length > sizeof(chunk)) {
      length = sizeof(chunk);
    }
    memcpy(chunk, kIndexHtmlGz + offset, length);
    server.chunkWrite(reinterpret_cast<const char *>(chunk), length);
    offset += length;
  }
  server.chunkResponseEnd();
}

static void handleReadings() {
  if (latestJson.length() == 0) {
    sendJson("null");
    return;
  }
  sendJson(latestJson);
}

static void handleHistory() {
  uint32_t after = 0;
  if (server.hasArg("after")) {
    after = (uint32_t)strtoul(server.arg("after").c_str(), nullptr, 10);
  }

  server.sendHeader("Cache-Control", "no-store");
  server.chunkResponseBegin("application/json");
  server.chunkWrite("[", 1);

  File file;
  bool open = storageReady && ring.capacity > 0;
  if (open) {
    file = LittleFS.open(RING_PATH, "r");
    open = (bool)file;
  }
  if (open) {
    uint32_t stored = ring.next < ring.capacity ? ring.next : ring.capacity;
    uint32_t oldest = ring.next < ring.capacity ? 0 : (ring.next % ring.capacity);
    bool first = true;
    char buf[768];
    for (uint32_t i = 0; i < stored && server.client().connected(); i++) {
      if ((i & 0x0f) == 0) {
        delay(0);
      }
      ReadingSample sample;
      uint32_t slot = (oldest + i) % ring.capacity;
      if (!readSample(file, slot, sample) || sample.unixTime <= after || sample.unixTime < CLOCK_VALID_AFTER) {
        continue;
      }
      int length = formatSample(sample, buf, sizeof(buf));
      if (length <= 0) {
        continue;
      }
      if (!first) {
        server.chunkWrite(",", 1);
      }
      server.chunkWrite(buf, length);
      first = false;
    }
    file.close();
  }

  server.chunkWrite("]", 1);
  server.chunkResponseEnd();
}

static void handleNotFound() {
  server.sendHeader("Cache-Control", "no-store");
  server.send(404, "text/plain", "not found");
}

void sensorBlankSample(ReadingSample &sample) {
  memset(&sample, 0, sizeof(sample));
  for (int i = 0; i < SENSOR_SERVER_MAX_PROBES; i++) {
    sample.probes[i].celsiusX100 = INT16_MIN;
  }
}

void sensorServerBegin() {
  beginStorage();
  server.on("/", HTTP_GET, handleRoot);
  server.on("/readings", HTTP_GET, handleReadings);
  server.on("/history", HTTP_GET, handleHistory);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("Sensor server on port 80");
}

bool sensorClockReady() {
  return (uint32_t)time(nullptr) > CLOCK_VALID_AFTER;
}

bool sensorFormatTime(uint32_t unixTime, char *out, size_t outLen) {
  time_t utc = (time_t)unixTime;
  struct tm localTm;
  struct tm utcTm;
  if (localtime_r(&utc, &localTm) == nullptr || gmtime_r(&utc, &utcTm) == nullptr) {
    return false;
  }
  int localSec = localTm.tm_hour * 3600 + localTm.tm_min * 60 + localTm.tm_sec;
  int utcSec = utcTm.tm_hour * 3600 + utcTm.tm_min * 60 + utcTm.tm_sec;
  int dayDelta = localTm.tm_year == utcTm.tm_year ? localTm.tm_yday - utcTm.tm_yday : (localTm.tm_year > utcTm.tm_year ? 1 : -1);
  int offset = dayDelta * 86400 + (localSec - utcSec);
  char sign = offset >= 0 ? '+' : '-';
  int absOffset = offset >= 0 ? offset : -offset;
  int wrote = snprintf(
    out,
    outLen,
    "%04d-%02d-%02dT%02d:%02d:%02d%c%02d:%02d",
    localTm.tm_year + 1900,
    localTm.tm_mon + 1,
    localTm.tm_mday,
    localTm.tm_hour,
    localTm.tm_min,
    localTm.tm_sec,
    sign,
    absOffset / 3600,
    (absOffset % 3600) / 60
  );
  return wrote > 0 && (size_t)wrote < outLen;
}

void sensorServerSync() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }
  if (!clockStarted) {
    configTzTime(TIME_ZONE, "pool.ntp.org", "time.nist.gov");
    clockStarted = true;
    struct tm now;
    if (getLocalTime(&now, 10000)) {
      Serial.println("Clock set");
    } else {
      Serial.println("Clock not set yet");
    }
  }
  if (!mdnsStarted && MDNS.begin("vermiculture")) {
    mdnsStarted = true;
    Serial.println("Chart: http://vermiculture.local/");
  }
  if (!chartPrinted) {
    chartPrinted = true;
    Serial.print("Chart: http://");
    Serial.println(WiFi.localIP());
  }
}

void sensorServerLoop() {
  sensorServerSync();
  server.handleClient();
}

void sensorServerSetLatest(const String &json) {
  latestJson = json;
}

bool sensorServerRecord(const ReadingSample &sample) {
  if (!storageReady || ring.capacity == 0 || sample.unixTime < CLOCK_VALID_AFTER) {
    return false;
  }
  File file = LittleFS.open(RING_PATH, "r+");
  if (!file) {
    Serial.println("Could not open the reading ring");
    return false;
  }
  uint32_t slot = ring.next % ring.capacity;
  size_t offset = sizeof(RingHeader) + (size_t)slot * sizeof(ReadingSample);
  if (offset > file.size() || !file.seek(offset) || file.write(reinterpret_cast<const uint8_t *>(&sample), sizeof(sample)) != sizeof(sample)) {
    file.close();
    Serial.println("Could not store a reading");
    return false;
  }
  ring.next++;
  bool headerOk = writeHeader(file);
  file.flush();
  file.close();
  if (!headerOk) {
    ring.next--;
    Serial.println("Could not update the reading ring");
    return false;
  }
  return true;
}
