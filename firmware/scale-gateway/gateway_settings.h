#pragma once

#include <Arduino.h>
#include <stdint.h>

#include "scale_protocol.h"

// Everything the gateway is told rather than compiled with. It lives in NVS,
// so it survives a reflash and no part of it belongs in the image.
namespace gatewaySettings {

// Opens the store. Nothing else here works before it has run.
void begin();

bool hasNetwork();
String network();
void setNetwork(const String &name);

bool hasPassphrase();
// Only the code that joins the network has any business calling this. Showing
// it back over the console would put it on whatever screen is nearby.
String passphrase();
void setPassphrase(const String &text);

// Where the server is, without a route on the end: the gateway posts weigh-ins
// and pings to different ones and appends whichever it needs. Empty until a url
// has been stored.
String serverUrl();
// A trailing slash is dropped, so the stored url and a route append cleanly
// however the url was typed.
void setServerUrl(const String &url);

bool hasSigningKey();
// The secret every delivery is signed with. Like the passphrase, only the code
// that uses it has any business reading it.
String signingKey();
void setSigningKey(const String &text);

// How many scales this gateway listens for. Zero until an address has been
// stored, and never more than MAX_SCALES.
size_t scaleCount();
// One of them, as it was typed, which is the form worth showing back.
// Meaningful only below scaleCount().
String scaleAddress(size_t index);
// Whether an advertisement carrying this address tail came from one of them.
// False for every scale while none is stored, which is what makes an
// unprovisioned gateway ignore the room rather than record it.
bool watchesScale(const uint8_t *addressTail);
// Takes the whole list at once, separated by spaces or commas, so what a board
// is told is what it listens for rather than an addition to whatever it held
// before. Rejects the lot — leaving the stored list alone — if any one of them
// is not an address or there are more than MAX_SCALES of them.
bool setScaleAddresses(const String &text);

void forget();

}  // namespace gatewaySettings
