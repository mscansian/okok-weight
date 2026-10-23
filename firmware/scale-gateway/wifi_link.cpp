#include "wifi_link.h"

#include <WiFi.h>

#include "config.h"
#include "gateway_settings.h"

namespace {

// wl_status_t says only that the link is down; why it went down arrives on the
// disconnect event and nowhere else, so it is kept as it passes.
volatile uint8_t lastDisconnectReason = 0;
volatile uint32_t disconnects = 0;
bool wasConnected = false;

void rememberDisconnect(WiFiEvent_t event, WiFiEventInfo_t info) {
  lastDisconnectReason = info.wifi_sta_disconnected.reason;
  disconnects++;
}

// The SDK's own names are terse, so the cases that actually come up while
// provisioning get a plain-language gloss.
const char *disconnectHint(uint8_t reason) {
  switch (reason) {
    case WIFI_REASON_NO_AP_FOUND:  return " — no network with that name is in range";
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:  return " — usually a wrong passphrase";
    case WIFI_REASON_BEACON_TIMEOUT:  return " — the access point stopped answering";
    case WIFI_REASON_ASSOC_FAIL:  return " — the access point refused the association";
    default:  return "";
  }
}

}  // namespace

void beginWiFi() {
  WiFi.onEvent(rememberDisconnect, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
}

void connectWiFi() {
  if (!gatewaySettings::hasNetwork()) {
    Serial.println("wifi: no network stored — type 'help'");
    return;
  }

  String network = gatewaySettings::network();
  Serial.printf("wifi: connecting to %s\n", network.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(WIFI_POWER_SAVE);

  WiFi.begin(network.c_str(), gatewaySettings::passphrase().c_str());
}

void disconnectWiFi() {
  WiFi.disconnect(true, true);
}

bool awaitWiFi(uint32_t timeoutMs) {
  const uint32_t startedAt = millis();
  while (!isWiFiConnected() && millis() - startedAt < timeoutMs) {
    delay(50);
  }
  return isWiFiConnected();
}

bool isWiFiConnected() {
  return WiFi.status() == WL_CONNECTED;
}

String wifiAddress() {
  return WiFi.localIP().toString();
}

String hardwareAddress() {
  String address = WiFi.macAddress();
  // The server groups everything a gateway has ever sent under one address, so
  // the board spells its own the one way rather than however the SDK does.
  address.toLowerCase();
  return address;
}

String currentNetwork() {
  return WiFi.SSID();
}

bool wifiJustConnected() {
  bool isConnected = isWiFiConnected();
  bool cameUp = isConnected && !wasConnected;
  wasConnected = isConnected;
  if (cameUp) {
    lastDisconnectReason = 0;
  }
  return cameUp;
}

uint32_t disconnectCount() {
  return disconnects;
}

uint8_t disconnectReason() {
  return lastDisconnectReason;
}

String describeDisconnect() {
  if (lastDisconnectReason == 0) {
    return "no reason reported yet";
  }
  wifi_err_reason_t reason = static_cast<wifi_err_reason_t>(lastDisconnectReason);
  return String(WiFi.disconnectReasonName(reason)) + " (" + lastDisconnectReason + ")"
       + disconnectHint(lastDisconnectReason);
}
