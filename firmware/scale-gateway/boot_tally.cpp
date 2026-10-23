#include "boot_tally.h"

#include <Arduino.h>
#include <esp_attr.h>
#include <esp_system.h>

namespace bootTally {
namespace {

// Held where a reset does not reach: the CPU goes down and these stay. Losing
// them to a power cut is the trade that keeps every boot out of the flash.
//
// Uninitialised RTC memory reads as a plausible count as readily as an
// implausible one, so the marker is what separates a tally that came through a
// reset from one that was never written at all.
RTC_NOINIT_ATTR uint32_t rtcBoots;
RTC_NOINIT_ATTR uint32_t rtcBrownouts;
RTC_NOINIT_ATTR uint32_t rtcRunMs;
RTC_NOINIT_ATTR uint32_t rtcMarker;

const uint32_t TALLY_KEPT = 0xB0075A11;

uint32_t previousRunMs = 0;
bool previousRunKnown = false;

}  // namespace

void recordBoot() {
  const bool kept = rtcMarker == TALLY_KEPT;
  if (!kept) {
    rtcBoots = 0;
    rtcBrownouts = 0;
    rtcMarker = TALLY_KEPT;
  }

  previousRunKnown = kept;
  previousRunMs = kept ? rtcRunMs : 0;
  rtcRunMs = 0;

  rtcBoots++;
  if (esp_reset_reason() == ESP_RST_BROWNOUT) {
    rtcBrownouts++;
  }
}

void recordUptime() {
  rtcRunMs = millis();
}

uint32_t boots() {
  return rtcBoots;
}

uint32_t brownouts() {
  return rtcBrownouts;
}

bool knowsPreviousUptime() {
  return previousRunKnown;
}

uint32_t previousUptimeMs() {
  return previousRunMs;
}

}  // namespace bootTally
