#pragma once

#include <stddef.h>
#include <stdint.h>

#include "scale_protocol.h"

// A UUID in its hex form, its four dashes and the terminator.
static const size_t EVENT_ID_LENGTH = 37;

// A weigh-in the gateway is ready to hand on: what the scale said, how well it
// was heard, and the name the gateway gave the event.
struct WeighIn {
  // Names this weigh-in rather than this delivery of it, so a server can tell a
  // repeated delivery from a second trip to the scale.
  char eventId[EVENT_ID_LENGTH];
  ScaleReading scale;
  int rssi;
  char address[18];
  // How many locked advertisements this weigh-in was built from. The weight
  // handed on is the last of them, so a weigh-in built from only one or two is
  // one whose end the receiver nearly missed — which makes this the number to
  // watch whenever the scan listens for less than the whole broadcast.
  uint16_t locks;
};

// Starts passive scanning. The scale never accepts a connection, so nothing
// here ever transmits.
void beginScanning();

// The scan stops of its own accord now and then; this puts it back.
void keepScanning();

// Hands over a weigh-in once the scale has stopped repeating it, and reports
// whether there was one to hand over.
bool takeSettledWeighIn(WeighIn *out);
