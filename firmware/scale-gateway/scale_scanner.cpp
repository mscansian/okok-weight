#include "scale_scanner.h"

#include <Arduino.h>
#include <BLEAdvertisedDevice.h>
#include <BLEDevice.h>
#include <BLEScan.h>
#include <esp_random.h>
#include <string.h>

#include "config.h"
#include "gateway_settings.h"

namespace {

// The receiver listens in one of two rhythms. Nothing is happening almost all
// the time, and that idle listening is the whole of what the radio costs across
// a day; but the end of a weigh-in is the part that cannot be sampled, because
// the weight handed on is the last lock of it. So the first lock buys continuous
// listening and the weigh-in settling gives it back.
enum class ScanPace { Idle, WeighIn };

ScanPace pace = ScanPace::Idle;

// A weigh-in the gateway is part-way through hearing, and when it last heard
// anything of it. One per scale it may listen for, because a lock belongs to a
// weigh-in by which scale sent it and by nothing else: with two scales in the
// room, a single slot would count one scale's locks against the other's
// weigh-in and hand on a weight nobody was standing on.
struct InFlight {
  bool active;
  WeighIn weighIn;
  uint32_t lastLockedAt;
};

InFlight inFlight[MAX_SCALES];
portMUX_TYPE inFlightLock = portMUX_INITIALIZER_UNLOCKED;

// Writes a version 4 UUID. esp_random() draws from the hardware entropy source
// while the RF subsystem is running, which BLE scanning keeps it doing for as
// long as this gateway is doing anything at all.
void makeEventId(char *out) {
  uint8_t bytes[16];
  for (size_t i = 0; i < sizeof(bytes); i += 4) {
    uint32_t word = esp_random();
    memcpy(bytes + i, &word, sizeof(word));
  }
  bytes[6] = (bytes[6] & 0x0F) | 0x40;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;

  snprintf(
    out, EVENT_ID_LENGTH, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", bytes[0], bytes[1],
    bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7], bytes[8], bytes[9], bytes[10], bytes[11], bytes[12],
    bytes[13], bytes[14], bytes[15]
  );
}

// Where this scale's weigh-in is being built: the slot already holding one, or
// a free slot to start one in. Called with the lock held.
//
// There are as many slots as a gateway may watch scales, so wanting a slot and
// finding none means one is still held by a scale that has just been taken off
// the watched list. That slot settles and frees itself within
// WEIGH_IN_SETTLE_MS, well inside the 2.3 s the scale goes on repeating itself,
// so the weigh-in is joined a few repeats late rather than missed.
InFlight *slotFor(const uint8_t *addressTail) {
  InFlight *free = nullptr;
  for (InFlight &slot : inFlight) {
    if (slot.active && memcmp(slot.weighIn.scale.addressTail, addressTail, SCALE_ADDRESS_TAIL_LENGTH) == 0) {
      return &slot;
    }
    if (!slot.active && free == nullptr) {
      free = &slot;
    }
  }
  return free;
}

class ScaleListener : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice device) override {
    String manufacturerData = device.getManufacturerData();
    WeighIn weighIn;
    bool locked = decodeScalePayload(
      reinterpret_cast<const uint8_t *>(manufacturerData.c_str()), manufacturerData.length(), &weighIn.scale
    );
    if (!locked || !gatewaySettings::watchesScale(weighIn.scale.addressTail)) {
      return;
    }

    weighIn.rssi = device.getRSSI();
    snprintf(weighIn.address, sizeof(weighIn.address), "%s", device.getAddress().toString().c_str());
    makeEventId(weighIn.eventId);
    weighIn.locks = 1;
    uint32_t heardAt = millis();

    portENTER_CRITICAL(&inFlightLock);
    InFlight *slot = slotFor(weighIn.scale.addressTail);
    if (slot != nullptr) {
      // Settling onto the scale locks it several times over, and all of those
      // locks are the one weigh-in, so the id is minted with the first of them
      // and the later ones inherit it and count themselves against it.
      if (slot->active) {
        memcpy(weighIn.eventId, slot->weighIn.eventId, EVENT_ID_LENGTH);
        weighIn.locks = slot->weighIn.locks + 1;
      }
      slot->weighIn = weighIn;
      slot->lastLockedAt = heardAt;
      slot->active = true;
    }
    portEXIT_CRITICAL(&inFlightLock);
  }
};

// Whether the gateway is part-way through hearing a weigh-in from any scale,
// which is what buys the faster of the two listening rhythms.
bool anyInFlight() {
  bool any = false;
  portENTER_CRITICAL(&inFlightLock);
  for (const InFlight &slot : inFlight) {
    if (slot.active) {
      any = true;
      break;
    }
  }
  portEXIT_CRITICAL(&inFlightLock);
  return any;
}

// A scan takes new timings only between runs, so this stops one if it is going.
void listenAt(ScanPace next) {
  BLEScan *scan = BLEDevice::getScan();
  if (scan->isScanning()) {
    scan->stop();
  }
  scan->setWindow(SCAN_WINDOW_MS);
  scan->setInterval(next == ScanPace::Idle ? IDLE_SCAN_INTERVAL_MS : WEIGH_IN_SCAN_INTERVAL_MS);
  scan->start(0, nullptr, false);
  pace = next;
}

}  // namespace

void beginScanning() {
  // The controller the core starts is dual mode, and this gateway does nothing
  // but scan. Handing the Classic half back before the controller comes up
  // leaves that memory to the heap, which is where verifying the server's
  // certificate chain finds room for the bignums it multiplies.
  btMemRelease(BT_MODE_CLASSIC_BT);

  BLEDevice::init("");
  BLEScan *scan = BLEDevice::getScan();

  // Everything the scale reports is in the advertisement itself, so passive
  // scanning is enough and the gateway never has to transmit.
  scan->setActiveScan(false);
  // The payload changes from packet to packet, so the transition into the locked
  // state only survives if repeat advertisements from the same address are
  // delivered — which is what wanting duplicates here asks for.
  scan->setAdvertisedDeviceCallbacks(new ScaleListener(), true, true);
  listenAt(ScanPace::Idle);
}

void keepScanning() {
  // The pace changes here rather than where a lock is noticed, because that runs
  // in the BLE stack's own task and a scan cannot be restarted from inside its
  // own callback. A weigh-in is broadcast for far longer than a turn of the main
  // loop, so arriving at the switch an iteration late costs nothing.
  ScanPace wanted = anyInFlight() ? ScanPace::WeighIn : ScanPace::Idle;
  if (wanted != pace) {
    listenAt(wanted);
    return;
  }

  BLEScan *scan = BLEDevice::getScan();
  if (!scan->isScanning()) {
    Serial.println("ble: scan stopped, restarting");
    scan->start(0, nullptr, false);
  }
}

// One weigh-in per call, because the caller comes back in a turn of the main
// loop and two scales that settled together are delivered a few milliseconds
// apart — which nothing downstream can tell from together, since a delivery says
// how long ago its weigh-in was taken rather than in what order it was handed on.
bool takeSettledWeighIn(WeighIn *out) {
  uint32_t now = millis();
  bool taken = false;

  portENTER_CRITICAL(&inFlightLock);
  for (InFlight &slot : inFlight) {
    if (slot.active && now - slot.lastLockedAt > WEIGH_IN_SETTLE_MS) {
      *out = slot.weighIn;
      slot.active = false;
      taken = true;
      break;
    }
  }
  portEXIT_CRITICAL(&inFlightLock);

  return taken;
}
