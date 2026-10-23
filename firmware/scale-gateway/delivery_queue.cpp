#include "delivery_queue.h"

namespace {

struct PendingDelivery {
  WeighIn weighIn;
  uint32_t takenAt;
};

// A ring, so the queue draining from the front costs nothing to the weigh-ins
// still behind it.
PendingDelivery pending[DELIVERY_QUEUE_CAPACITY];
size_t oldest = 0;
size_t held = 0;

void dropOldest() {
  oldest = (oldest + 1) % DELIVERY_QUEUE_CAPACITY;
  held--;
}

// The board's clock wraps every 49.7 days, and unsigned arithmetic carries an
// age across that wrap as long as the age itself is the smaller of the two.
// Nothing is held anywhere near long enough for it not to be.
uint32_t ageOf(const PendingDelivery &delivery, uint32_t now) {
  return now - delivery.takenAt;
}

}  // namespace

bool queueDelivery(const WeighIn &weighIn, uint32_t now) {
  bool wasFull = held == DELIVERY_QUEUE_CAPACITY;
  if (wasFull) {
    dropOldest();
  }

  PendingDelivery &slot = pending[(oldest + held) % DELIVERY_QUEUE_CAPACITY];
  slot.weighIn = weighIn;
  slot.takenAt = now;
  held++;
  return wasFull;
}

size_t dropExpiredDeliveries(uint32_t now) {
  size_t dropped = 0;
  // Weigh-ins queue in the order they were taken, so ages only fall along the
  // queue and the first one still young ends the sweep.
  while (held > 0 && ageOf(pending[oldest], now) > DELIVERY_MAX_AGE_MS) {
    dropOldest();
    dropped++;
  }
  return dropped;
}

size_t pendingDeliveries() {
  return held;
}

bool nextDelivery(WeighIn *out, uint32_t *ageMs, uint32_t now) {
  if (held == 0) {
    return false;
  }
  *out = pending[oldest].weighIn;
  *ageMs = ageOf(pending[oldest], now);
  return true;
}

void deliveryAccepted() {
  if (held > 0) {
    dropOldest();
  }
}
