#pragma once

#include <Arduino.h>
#include <stdint.h>
#include <stdio.h>

// Four temperature probes match the wiring in the sketch.
// A fifth probe is still reported in the latest JSON, but the on-board history keeps these.
static const int SENSOR_SERVER_MAX_PROBES = 4;

struct __attribute__((packed)) ProbeSample {
  uint8_t address[8];
  int16_t celsiusX100;  // INT16_MIN when the probe did not answer
};

struct __attribute__((packed)) ReadingSample {
  uint32_t unixTime;
  uint8_t probeCount;
  ProbeSample probes[SENSOR_SERVER_MAX_PROBES];
  uint8_t moisturePin;
  int16_t moistureRaw;      // INT16_MIN when no moisture probe was read
  int16_t moisturePercent;  // INT16_MIN when no moisture probe was read
};

static_assert(sizeof(ReadingSample) == 50, "ReadingSample size changed; bump the ring version");

inline bool sensorFormatCenti(int16_t centi, char *out, size_t outLen) {
  if (centi == INT16_MIN) {
    return false;
  }
  int value = centi;
  const char *sign = "";
  if (value < 0) {
    sign = "-";
    value = -value;
  }
  int wrote = snprintf(out, outLen, "%s%d.%02d", sign, value / 100, value % 100);
  return wrote > 0 && (size_t)wrote < outLen;
}

void sensorBlankSample(ReadingSample &sample);
void sensorServerBegin();
void sensorServerSync();
void sensorServerLoop();
bool sensorClockReady();
bool sensorFormatTime(uint32_t unixTime, char *out, size_t outLen);
void sensorServerSetLatest(const String &json);
bool sensorServerRecord(const ReadingSample &sample);
