#pragma once

#include <stddef.h>
#include <stdint.h>

// Decodes the scale's advertisement payload. Nothing here touches the BLE
// stack, WiFi or NVS, which is what lets it be built and tested on a host
// against the captures in specs/.

// The scale repeats the last five octets of its own address in every packet,
// and that is all the advertisement carries of it. The leading octet is never
// transmitted, so it plays no part in recognising the device.
static const size_t SCALE_ADDRESS_TAIL_LENGTH = 5;

// What the scale itself reports. How well it was heard and what the gateway
// named it belong to the weigh-in that carries this, not to the scale.
struct ScaleReading {
  float weightKg;
  uint16_t fieldB;
  uint8_t sequence;
  // Which scale said it, in the only form the advertisement carries.
  uint8_t addressTail[SCALE_ADDRESS_TAIL_LENGTH];
};

// Reports whether the payload held a locked reading from a scale, and fills
// `out` when it did. A packet from another kind of device, one taken before the
// weight settled and a malformed one are all a plain false: the gateway sees far
// more packets it should ignore than packets it should not, so none of them is
// an error.
//
// Which scale sent it comes back in the reading rather than going in as a filter.
// A gateway listens for several and can be told to listen for different ones
// while it runs, so recognising an address is a question about the gateway's
// settings and this file knows only the protocol.
bool decodeScalePayload(const uint8_t *payload, size_t length, ScaleReading *out);

// Reads an address written the way a scanner reports it, colon- or
// dash-separated, in either case, and keeps the trailing five octets. A
// six-octet address and the five-octet tail alone are both understood.
bool parseScaleAddressTail(const char *text, uint8_t *tail);
