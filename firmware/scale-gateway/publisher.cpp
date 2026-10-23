#include "publisher.h"

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <NetworkClient.h>
#include <NetworkClientSecure.h>

#include "boot_tally.h"
#include "config.h"
#include "delivery_queue.h"
#include "gateway_settings.h"
#include "request_signature.h"
#include "wifi_link.h"

// The server serves its routes under one versioned prefix, and verifies the
// signature over the path a request arrives at, so the prefix is spelled here
// the way the server spells it rather than left to the stored url.
#define API_PREFIX "/api/v1"

namespace {

// A handshake gets a budget of the same order as the request it opens. The
// library's own default is two minutes, and everything else the gateway does —
// scanning, the console, the queue — waits in the same loop for this to return.
const uint32_t HANDSHAKE_TIMEOUT_S = 10;

// A server that is not answering stays that way for far longer than a weigh-in
// takes, so the queue is retried on a timer rather than as fast as the loop
// runs. It costs a backlog at most this long to start moving once the link is
// back, against a POST attempt every 50 ms for as long as the outage lasts.
const uint32_t DELIVERY_RETRY_INTERVAL_MS = 30000;

const char *const READINGS_ROUTE = API_PREFIX "/readings";

bool pingOwed = false;
uint32_t lastAttemptAt = 0;
uint32_t lastPingAt = 0;

// The path the server will see, which is what it verifies the signature over.
// The stored url may carry a path of its own — a server sitting behind a prefix
// — and that prefix is part of what arrives, so it is part of what is signed.
String requestPath(const String &route) {
  const String url = gatewaySettings::serverUrl();
  const int afterScheme = url.indexOf("://");
  const int prefix = url.indexOf('/', afterScheme < 0 ? 0 : afterScheme + 3);
  return (prefix < 0 ? String() : url.substring(prefix)) + route;
}

// Signs a request with the stored key. The signature is computed here rather
// than by whoever built the body so that every request that leaves carries one:
// the server refuses anything unsigned, and a ping that skipped this would
// answer for a route a weigh-in cannot get through.
bool sign(const String &route, const String &body, char *signature) {
  return gatewaySettings::hasSigningKey() &&
         signRequest(
           gatewaySettings::signingKey().c_str(), "POST", requestPath(route).c_str(), body.c_str(), signature
         );
}

// Returns the HTTP status, or a negative HTTPClient error code.
//
// The transport is chosen here rather than left to HTTPClient's url-only
// begin(), so that the handshake timeout below applies to it.
int postJson(const String &route, const String &body, const char *signature, uint32_t *elapsedMs) {
  const String url = gatewaySettings::serverUrl() + route;
  const bool secure = url.startsWith("https:");

  // https here buys the encryption and not the identity: the certificate is
  // accepted unverified. Verifying it means a chain walk and a public-key
  // operation on top of two 16 KB record buffers, which is more heap than this
  // board has left once BLE and WiFi have taken theirs.
  //
  // What the gateway would lose to an impostor is bounded by the signature. The
  // key is never sent — the body is signed with it — so a server that is not
  // this one cannot learn it, and cannot forge a weigh-in into the history
  // without it. It can claim a delivery landed, which drops the weigh-in from
  // the queue, so the cost of the trade is readings lost rather than invented.
  NetworkClient tcp;
  NetworkClientSecure tls;
  if (secure) {
    tls.setInsecure();
    tls.setHandshakeTimeout(HANDSHAKE_TIMEOUT_S);
  }
  NetworkClient &transport = secure ? static_cast<NetworkClient &>(tls) : tcp;

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(5000);
  if (!http.begin(transport, url)) {
    return HTTPC_ERROR_CONNECTION_REFUSED;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader(SIGNATURE_HEADER, signature);

  // Verifying the chain multiplies bignums that are allocated as it goes, which
  // makes a handshake the largest demand this board puts on the heap. Both
  // numbers are read before the attempt, because a failed one has given its
  // memory back by the time it returns, and because free memory in pieces too
  // small to use reads as free memory in every measure but the second.
  const unsigned freeHeap = ESP.getFreeHeap();
  const unsigned largestBlock = ESP.getMaxAllocHeap();

  uint32_t started = millis();
  int status = http.POST(body);
  if (elapsedMs != nullptr) {
    *elapsedMs = millis() - started;
  }

  // A rejected certificate and an unreachable port arrive as the same negative
  // status, so the one the caller reports cannot tell them apart. mbedTLS keeps
  // the reason it refused, and it is the difference between a wrong url and a
  // server that is simply down.
  if (secure && status < 0) {
    char reason[128];
    if (tls.lastError(reason, sizeof(reason)) != 0) {
      Serial.printf("tls: %s\n", reason);
    }
  }

  if (status < 0) {
    Serial.printf("heap: %u free, largest block %u, going in\n", freeHeap, largestBlock);
  }

  http.end();
  return status;
}

bool readyToPost(const char *attempt) {
  if (gatewaySettings::serverUrl().isEmpty()) {
    Serial.printf("%s: no url stored\n", attempt);
    return false;
  }
  if (!isWiFiConnected()) {
    Serial.printf("%s: wifi is down\n", attempt);
    return false;
  }
  if (!gatewaySettings::hasSigningKey()) {
    Serial.printf("%s: no signing key stored\n", attempt);
    return false;
  }
  return true;
}

// The gateway has no clock of its own — the ESP32's survives neither a power cut
// nor the wait for a network to ask the time of — so a weigh-in is timed by how
// long ago it was taken, which the board can answer from the moment it boots.
// The server holds the clock and does the subtraction.
void writeBody(char *body, size_t size, const WeighIn &weighIn, uint32_t ageMs) {
  snprintf(
    body, size,
    "{\"event_id\":\"%s\",\"weight_kg\":%.2f,\"field_b\":%u,\"sequence\":%u,\"rssi\":%d,\"address\":\"%s\","
    "\"age_ms\":%u,\"locks\":%u}",
    weighIn.eventId, weighIn.scale.weightKg, weighIn.scale.fieldB, weighIn.scale.sequence, weighIn.rssi,
    weighIn.address, ageMs, weighIn.locks
  );
}

// Sends what is waiting, oldest first, and stops at the first refusal: a server
// that has stopped answering should be asked once per round, not once per
// weigh-in held. The age is read for each one as it goes out, so a weigh-in that
// waited behind a slow delivery still reports its own.
void attemptDeliveries() {
  lastAttemptAt = millis();
  if (gatewaySettings::serverUrl().isEmpty() || !isWiFiConnected() || !gatewaySettings::hasSigningKey()) {
    return;
  }

  WeighIn weighIn;
  uint32_t ageMs = 0;
  while (nextDelivery(&weighIn, &ageMs, millis())) {
    char body[320];
    writeBody(body, sizeof(body), weighIn, ageMs);

    char signature[SIGNATURE_LENGTH];
    if (!sign(READINGS_ROUTE, body, signature)) {
      Serial.println("publish: cannot sign — the weigh-in stays queued");
      return;
    }

    int status = postJson(READINGS_ROUTE, body, signature, nullptr);
    if (status < 200 || status >= 300) {
      Serial.printf(
        "publish: failed, status %d (%s)\n", status, HTTPClient::errorToString(status).c_str()
      );
      return;
    }
    deliveryAccepted();
  }
}

// Why the board last came up. A brownout is a power supply to fix and a panic is
// firmware to fix, and an uptime that keeps returning to zero says neither. The
// tally beside it in the ping is what turns one of these into a rate.
const char *resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON:  return "power-on";
    case ESP_RST_EXT:  return "external";
    case ESP_RST_SW:  return "software";
    case ESP_RST_PANIC:  return "panic";
    case ESP_RST_INT_WDT:  return "interrupt-watchdog";
    case ESP_RST_TASK_WDT:  return "task-watchdog";
    case ESP_RST_WDT:  return "watchdog";
    case ESP_RST_DEEPSLEEP:  return "deep-sleep";
    case ESP_RST_BROWNOUT:  return "brownout";
    default:  return "unknown";
  }
}

// An SSID is whatever whoever set the access point up made it, so the characters
// that would end the string early or break out of it are escaped rather than
// trusted. A body the server cannot parse is a ping that fails for good.
String asJsonString(const String &text) {
  String quoted = "\"";
  for (size_t i = 0; i < text.length(); i++) {
    const char character = text[i];
    if (character == '"' || character == '\\') {
      quoted += '\\';
      quoted += character;
    } else if (static_cast<uint8_t>(character) < 0x20) {
      char escape[7];
      snprintf(escape, sizeof(escape), "\\u%04x", character);
      quoted += escape;
    } else {
      quoted += character;
    }
  }
  return quoted + "\"";
}

// The scales this gateway was told to listen for, as a JSON array. Empty rather
// than absent when it was told none, because a board listening for nothing is
// exactly the board this field exists to reveal.
String watchedScales() {
  String array = "[";
  for (size_t i = 0; i < gatewaySettings::scaleCount(); i++) {
    if (i > 0) {
      array += ',';
    }
    array += asJsonString(gatewaySettings::scaleAddress(i));
  }
  return array + "]";
}

// What the board can say about itself that nothing else can. The uptime comes
// from the timer rather than millis() because millis() turns over every seven
// weeks, and an uptime that wraps would read as the reboot it exists to reveal.
//
// The heap is read here rather than at the moment of the POST because this is
// the headroom the POST is about to be given, and a ping that lands is one that
// found enough of it — the interesting readings are the ones just before a board
// stops being able to send them at all.
// How long the run before this one lasted, which the board can only answer for a
// reset it came through rather than a power cut it did not.
String previousUptime() {
  if (!bootTally::knowsPreviousUptime()) {
    return "null";
  }
  return String(static_cast<unsigned>(bootTally::previousUptimeMs()));
}

String pingBody() {
  const String scales = watchedScales();
  // Wide enough for a full list of scales beside an SSID whose every character
  // has to be escaped, because a body truncated to fit is a body the server
  // cannot parse.
  char body[1024];
  snprintf(
    body, sizeof(body),
    "{\"scale_addresses\":%s,\"ssid\":%s,\"wifi_rssi\":%d,\"queued\":%u,\"uptime_ms\":%lld,"
    "\"interval_ms\":%u,\"firmware_version\":\"%s\",\"reset_reason\":\"%s\",\"boots\":%u,"
    "\"brownouts\":%u,\"free_heap\":%u,\"largest_free_block\":%u,\"min_free_heap\":%u,"
    "\"previous_uptime_ms\":%s,\"wifi_disconnects\":%u,\"wifi_disconnect_reason\":%u,"
    "\"cpu_mhz\":%u,\"xtal_mhz\":%u}",
    scales.c_str(), asJsonString(currentNetwork()).c_str(), WiFi.RSSI(),
    static_cast<unsigned>(pendingDeliveries()), esp_timer_get_time() / 1000,
    static_cast<unsigned>(PING_INTERVAL_MS), FIRMWARE_VERSION,
    resetReason(), static_cast<unsigned>(bootTally::boots()), static_cast<unsigned>(bootTally::brownouts()),
    static_cast<unsigned>(ESP.getFreeHeap()), static_cast<unsigned>(ESP.getMaxAllocHeap()),
    static_cast<unsigned>(ESP.getMinFreeHeap()), previousUptime().c_str(),
    static_cast<unsigned>(disconnectCount()), static_cast<unsigned>(disconnectReason()),
    static_cast<unsigned>(getCpuFrequencyMhz()), static_cast<unsigned>(getXtalFrequencyMhz())
  );
  return body;
}

}  // namespace

void pingServer() {
  // The interval runs from the last attempt rather than the last success, so a
  // server that is down is asked once a round like everything else here.
  lastPingAt = millis();
  if (!readyToPost("ping")) {
    return;
  }

  const String route = API_PREFIX "/devices/" + hardwareAddress() + "/ping";
  const String body = pingBody();
  char signature[SIGNATURE_LENGTH];
  if (!sign(route, body, signature)) {
    Serial.println("ping: cannot sign the request");
    return;
  }

  uint32_t elapsed = 0;
  const String url = gatewaySettings::serverUrl() + route;
  int status = postJson(route, body, signature, &elapsed);
  if (status >= 200 && status < 300) {
    Serial.printf("ping: %s answered %d in %u ms\n", url.c_str(), status, elapsed);
  } else {
    Serial.printf("ping: %s failed, status %d (%s)\n", url.c_str(), status, HTTPClient::errorToString(status).c_str());
  }
}

void owePing() {
  pingOwed = true;
}

void pingIfDue() {
  if (!isWiFiConnected()) {
    return;
  }
  if (!pingOwed && millis() - lastPingAt < PING_INTERVAL_MS) {
    return;
  }
  pingOwed = false;
  pingServer();
}

size_t heldWeighIns() {
  return pendingDeliveries();
}

void publish(const WeighIn &weighIn) {
  Serial.printf(
    "reading: %.2f kg  field_b=%u (0x%04X)  seq=%u  rssi=%d dBm  locks=%u  from=%s  event=%s\n",
    weighIn.scale.weightKg, weighIn.scale.fieldB, weighIn.scale.fieldB, weighIn.scale.sequence, weighIn.rssi,
    weighIn.locks, weighIn.address, weighIn.eventId
  );

  if (queueDelivery(weighIn, millis())) {
    Serial.printf(
      "publish: %u weigh-in(s) already waiting — dropped the oldest to make room\n",
      static_cast<unsigned>(DELIVERY_QUEUE_CAPACITY)
    );
  }

  attemptDeliveries();

  size_t waiting = pendingDeliveries();
  if (waiting > 0) {
    Serial.printf("publish: holding %u weigh-in(s) until the server answers\n", static_cast<unsigned>(waiting));
  }
}

void deliverPending() {
  uint32_t now = millis();

  size_t expired = dropExpiredDeliveries(now);
  if (expired > 0) {
    Serial.printf(
      "publish: gave up on %u weigh-in(s) held longer than 24 h\n", static_cast<unsigned>(expired)
    );
  }

  if (pendingDeliveries() == 0 || now - lastAttemptAt < DELIVERY_RETRY_INTERVAL_MS) {
    return;
  }
  attemptDeliveries();
}
