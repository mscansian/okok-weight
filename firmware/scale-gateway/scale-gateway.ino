// Listens for a BLE bathroom scale that broadcasts its reading in the clear and
// forwards each completed weigh-in to a server over WiFi. The scale never
// accepts a connection, so this is a passive receiver: it only ever scans.
//
// Network settings are typed into the serial console and kept in NVS. The
// firmware image is therefore the same on every board and carries no
// credentials, so it can be rebuilt, shared or committed freely.
//
// This file is the wiring. Each concern it wires together lives beside it:
//   boot_tally        how often this board has come up, and why
//   scale_protocol    the advertisement payload, and nothing else
//   scale_scanner     scanning, and holding a weigh-in until it settles
//   delivery_queue    the weigh-ins the server has still to be told about
//   publisher         getting a settled weigh-in to the server
//   request_signature proving a delivery came from this gateway
//   gateway_settings  everything the gateway is told rather than compiled with
//   wifi_link         joining the network and explaining why it did not
//   console           the serial commands

#include <Arduino.h>

#include "boot_tally.h"
#include "config.h"
#include "console.h"
#include "gateway_settings.h"
#include "publisher.h"
#include "scale_scanner.h"
#include "wifi_link.h"

static const uint32_t WIFI_REPORT_INTERVAL_MS = 30000;

void setup() {
  setCpuFrequencyMhz(CPU_FREQUENCY_MHZ);

  Serial.begin(115200);
  delay(200);
  Serial.println("\nscale gateway");

  bootTally::recordBoot();

  beginWiFi();
  gatewaySettings::begin();
  if (gatewaySettings::scaleCount() == 0) {
    Serial.println("no scale addresses stored — nothing will be recognised until at least one is set");
  }
  if (!gatewaySettings::hasSigningKey()) {
    Serial.println("no signing key stored — the server refuses unsigned deliveries, so none will land");
  }

  // A boot is not the moment to read the manual, and a board that resets a few
  // times should not bury what it did between two screenfuls of it.
  Serial.println("type 'help' for commands");
  connectWiFi();
  if (!awaitWiFi(WIFI_SETTLE_TIMEOUT_MS)) {
    Serial.println("wifi: not up yet — scanning anyway, weigh-ins will queue");
  }

  beginScanning();
  Serial.println("ble: scanning");
}

void loop() {
  static uint32_t lastWiFiReport = 0;
  uint32_t now = millis();

  readCommands();
  bootTally::recordUptime();

  WeighIn weighIn;
  if (takeSettledWeighIn(&weighIn)) {
    publish(weighIn);
  }

  if (wifiJustConnected()) {
    Serial.printf("wifi: connected as %s\n", wifiAddress().c_str());
    owePing();
  }
  pingIfDue();
  deliverPending();

  // The radio retries on its own because auto-reconnect is on, and asking again
  // while an attempt is already in flight only logs an error, so this reports
  // rather than intervenes.
  if (gatewaySettings::hasNetwork() && !isWiFiConnected() && now - lastWiFiReport > WIFI_REPORT_INTERVAL_MS) {
    lastWiFiReport = now;
    Serial.printf("wifi: still not connected — %s\n", describeDisconnect().c_str());
  }

  keepScanning();

  delay(50);
}
