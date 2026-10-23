#include "gateway_settings.h"

#include <Preferences.h>
#include <string.h>

#include "config.h"

namespace gatewaySettings {
namespace {

// NVS keys are capped at 15 characters.
const char *NAMESPACE = "scale";
const char *KEY_NETWORK = "network";
const char *KEY_PASSPHRASE = "passphrase";
const char *KEY_SERVER_URL = "server-url";
const char *KEY_SIGNING_KEY = "signing-key";
// Holds the whole list, separated by spaces. The key keeps the name it had when
// it held one address, so a board provisioned before this firmware reads its
// scale back as a list of one and keeps working across the reflash.
const char *KEY_SCALES = "scale-addr";

Preferences store;

// The url is read on every weigh-in and the tails on every advertisement, which
// is far too often to go back to NVS for. Keeping them here means every write
// has to refresh them, which is why the writes go through this file.
String cachedServerUrl;
String cachedAddresses[MAX_SCALES];
uint8_t cachedTails[MAX_SCALES][SCALE_ADDRESS_TAIL_LENGTH];
size_t cachedScales = 0;

// What parseScaleList answers with when the text was not a list of addresses.
// Distinct from zero, which is what an empty text is.
const size_t NOT_A_LIST = static_cast<size_t>(-1);

// Reads addresses separated by spaces or commas, so a list can be typed either
// way and pasted from wherever it was written down. All or nothing: one token
// that is not an address makes the whole text one, so a mistyped second address
// can never half-replace a working list.
size_t parseScaleList(const String &text, String *addresses, uint8_t tails[][SCALE_ADDRESS_TAIL_LENGTH]) {
  size_t count = 0;
  size_t cursor = 0;

  while (cursor < text.length()) {
    while (cursor < text.length() && (text[cursor] == ' ' || text[cursor] == ',')) {
      cursor++;
    }
    size_t end = cursor;
    while (end < text.length() && text[end] != ' ' && text[end] != ',') {
      end++;
    }
    if (end == cursor) {
      break;
    }

    if (count == MAX_SCALES) {
      return NOT_A_LIST;
    }
    String token = text.substring(cursor, end);
    if (!parseScaleAddressTail(token.c_str(), tails[count])) {
      return NOT_A_LIST;
    }
    addresses[count++] = token;
    cursor = end;
  }

  return count;
}

// Anything the store holds got past setScaleAddresses, so text that will not
// parse here is a store this firmware did not write — a board provisioned by a
// build that allowed more scales than this one. It listens for none of them
// rather than an arbitrary few, and setup() says so at boot.
void reloadScales() {
  size_t parsed = parseScaleList(store.getString(KEY_SCALES, ""), cachedAddresses, cachedTails);
  cachedScales = parsed == NOT_A_LIST ? 0 : parsed;
}

}  // namespace

void begin() {
  store.begin(NAMESPACE, false);
  cachedServerUrl = store.getString(KEY_SERVER_URL, "");
  reloadScales();
}

bool hasNetwork() {
  return store.isKey(KEY_NETWORK);
}

String network() {
  return store.getString(KEY_NETWORK, "");
}

void setNetwork(const String &name) {
  store.putString(KEY_NETWORK, name);
}

bool hasPassphrase() {
  return store.isKey(KEY_PASSPHRASE);
}

String passphrase() {
  return store.getString(KEY_PASSPHRASE, "");
}

void setPassphrase(const String &text) {
  store.putString(KEY_PASSPHRASE, text);
}

String serverUrl() {
  return cachedServerUrl;
}

void setServerUrl(const String &url) {
  String base = url;
  while (base.endsWith("/")) {
    base.remove(base.length() - 1);
  }
  store.putString(KEY_SERVER_URL, base);
  cachedServerUrl = base;
}

bool hasSigningKey() {
  return store.isKey(KEY_SIGNING_KEY);
}

String signingKey() {
  return store.getString(KEY_SIGNING_KEY, "");
}

void setSigningKey(const String &text) {
  store.putString(KEY_SIGNING_KEY, text);
}

size_t scaleCount() {
  return cachedScales;
}

String scaleAddress(size_t index) {
  return index < cachedScales ? cachedAddresses[index] : String();
}

bool watchesScale(const uint8_t *addressTail) {
  for (size_t i = 0; i < cachedScales; i++) {
    if (memcmp(cachedTails[i], addressTail, SCALE_ADDRESS_TAIL_LENGTH) == 0) {
      return true;
    }
  }
  return false;
}

bool setScaleAddresses(const String &text) {
  String addresses[MAX_SCALES];
  uint8_t tails[MAX_SCALES][SCALE_ADDRESS_TAIL_LENGTH];
  size_t parsed = parseScaleList(text, addresses, tails);
  if (parsed == NOT_A_LIST || parsed == 0) {
    return false;
  }

  // Stored with the one separator, so a list typed with commas and a list typed
  // with spaces read back the same and the ping reports the same thing either
  // way. The addresses themselves are kept as they were written.
  String stored = addresses[0];
  for (size_t i = 1; i < parsed; i++) {
    stored += ' ';
    stored += addresses[i];
  }

  store.putString(KEY_SCALES, stored);
  reloadScales();
  return true;
}

void forget() {
  store.clear();
  cachedServerUrl = "";
  cachedScales = 0;
}

}  // namespace gatewaySettings
