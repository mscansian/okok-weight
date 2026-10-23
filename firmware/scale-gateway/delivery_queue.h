#pragma once

#include <stddef.h>
#include <stdint.h>

#include "scale_scanner.h"

// Weigh-ins taken but not yet delivered, oldest first. The scale is stood on
// whether or not there is a link to the server, so this is what stands between
// a dropped connection and a lost weigh-in.
//
// Nothing here touches the BLE stack, WiFi or NVS, and the current time is
// passed in rather than read, which is what lets it be built and tested on a
// host against the cases a board would take a day to reach.

// A weigh-in an hour for a day and then some — far more than a bathroom scale
// sees, and still only a few kilobytes of a queue that is almost always empty.
static const size_t DELIVERY_QUEUE_CAPACITY = 64;

// How long a weigh-in is worth delivering. Past this, a history missing the
// reading is better than one that gains a day-old weight the moment the link
// comes back.
static const uint32_t DELIVERY_MAX_AGE_MS = 24UL * 60 * 60 * 1000;

// Holds a weigh-in for delivery, and reports whether the queue was already
// full — in which case the oldest waiting weigh-in was dropped to make room.
bool queueDelivery(const WeighIn &weighIn, uint32_t now);

// Discards every weigh-in held longer than DELIVERY_MAX_AGE_MS, and returns how
// many went.
size_t dropExpiredDeliveries(uint32_t now);

size_t pendingDeliveries();

// The weigh-in to send next and how long ago it was taken, which is what the
// server needs to time it. False when nothing is waiting.
bool nextDelivery(WeighIn *out, uint32_t *ageMs, uint32_t now);

// Drops the weigh-in nextDelivery handed over, once the server has it.
void deliveryAccepted();
