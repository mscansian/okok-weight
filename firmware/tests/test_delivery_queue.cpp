// Exercises the queue that holds weigh-ins back over a dropped link. Builds on
// the host: `make firmware-test`. The clock is an argument rather than the
// board's, so a day of waiting and the 49.7-day wrap both cost nothing to reach.

#include <stdio.h>
#include <string.h>

#include "delivery_queue.h"

static int failures = 0;

static void checkThat(bool passed, const char *expression, int line) {
  if (!passed) {
    printf("  FAIL line %d: %s\n", line, expression);
    failures++;
  }
}

#define CHECK(expression) checkThat((expression), #expression, __LINE__)

static const uint32_t ONE_HOUR_MS = 60 * 60 * 1000;

// Weigh-ins are told apart by their event id here, since that is what the
// server uses to tell one delivery of a weigh-in from another.
static WeighIn weighInNamed(const char *eventId) {
  WeighIn weighIn;
  memset(&weighIn, 0, sizeof(weighIn));
  snprintf(weighIn.eventId, EVENT_ID_LENGTH, "%s", eventId);
  weighIn.scale.weightKg = 72.00f;
  return weighIn;
}

static void drainQueue() {
  while (pendingDeliveries() > 0) {
    deliveryAccepted();
  }
}

static void deliversInTheOrderTheWeighInsWereTaken() {
  printf("delivers in the order the weigh-ins were taken\n");

  CHECK(!queueDelivery(weighInNamed("first"), 1000));
  CHECK(!queueDelivery(weighInNamed("second"), 2000));
  CHECK(pendingDeliveries() == 2);

  WeighIn next;
  uint32_t ageMs = 0;
  CHECK(nextDelivery(&next, &ageMs, 5000));
  CHECK(strcmp(next.eventId, "first") == 0);
  // The age is what the server subtracts from arrival, so it is the gap between
  // the weigh-in and this delivery attempt, not the gap to the one before it.
  CHECK(ageMs == 4000);

  deliveryAccepted();
  CHECK(nextDelivery(&next, &ageMs, 5000));
  CHECK(strcmp(next.eventId, "second") == 0);
  CHECK(ageMs == 3000);

  deliveryAccepted();
  CHECK(pendingDeliveries() == 0);
  CHECK(!nextDelivery(&next, &ageMs, 5000));
}

// A weigh-in stays in the queue until the server has taken it, which is the
// whole point: a refused delivery must not consume it.
static void keepsAWeighInThatWasNotAccepted() {
  printf("keeps a weigh-in that was not accepted\n");

  queueDelivery(weighInNamed("held"), 1000);

  WeighIn next;
  uint32_t ageMs = 0;
  CHECK(nextDelivery(&next, &ageMs, 2000));
  CHECK(nextDelivery(&next, &ageMs, 3000));
  CHECK(pendingDeliveries() == 1);
  CHECK(ageMs == 2000);

  deliveryAccepted();
  CHECK(pendingDeliveries() == 0);

  drainQueue();
}

static void givesUpOnWeighInsHeldLongerThanADay() {
  printf("gives up on weigh-ins held longer than a day\n");

  queueDelivery(weighInNamed("stale"), 0);
  queueDelivery(weighInNamed("also-stale"), ONE_HOUR_MS);
  queueDelivery(weighInNamed("fresh"), 23 * ONE_HOUR_MS);

  // Two hours past the day the second one had, which leaves the third three
  // hours short of its own.
  const uint32_t now = 26 * ONE_HOUR_MS;
  CHECK(dropExpiredDeliveries(now) == 2);
  CHECK(pendingDeliveries() == 1);

  WeighIn next;
  uint32_t ageMs = 0;
  CHECK(nextDelivery(&next, &ageMs, now));
  CHECK(strcmp(next.eventId, "fresh") == 0);

  // Nothing is old enough to go a second time.
  CHECK(dropExpiredDeliveries(now) == 0);

  drainQueue();
}

// The limit is how long a weigh-in may be held, so one held exactly that long
// has not yet outstayed it.
static void keepsAWeighInUntilItIsPastTheLimit() {
  printf("keeps a weigh-in until it is past the limit\n");

  queueDelivery(weighInNamed("on-the-limit"), 0);
  CHECK(dropExpiredDeliveries(DELIVERY_MAX_AGE_MS) == 0);
  CHECK(pendingDeliveries() == 1);

  CHECK(dropExpiredDeliveries(DELIVERY_MAX_AGE_MS + 1) == 1);
  CHECK(pendingDeliveries() == 0);
}

static void keepsTheNewestWeighInsWhenTheQueueFills() {
  printf("keeps the newest weigh-ins when the queue fills\n");

  for (size_t i = 0; i < DELIVERY_QUEUE_CAPACITY; i++) {
    char eventId[EVENT_ID_LENGTH];
    snprintf(eventId, sizeof(eventId), "weigh-in-%zu", i);
    CHECK(!queueDelivery(weighInNamed(eventId), static_cast<uint32_t>(i)));
  }
  CHECK(pendingDeliveries() == DELIVERY_QUEUE_CAPACITY);

  // One too many: the oldest goes, and the queue is still full rather than
  // having grown.
  CHECK(queueDelivery(weighInNamed("newest"), 9999));
  CHECK(pendingDeliveries() == DELIVERY_QUEUE_CAPACITY);

  WeighIn next;
  uint32_t ageMs = 0;
  CHECK(nextDelivery(&next, &ageMs, 9999));
  CHECK(strcmp(next.eventId, "weigh-in-1") == 0);

  drainQueue();
}

// The board's clock wraps every 49.7 days, and a queue that lost track of an
// age there would either deliver nothing or expire everything.
static void carriesAnAgeAcrossTheClockWrapping() {
  printf("carries an age across the clock wrapping\n");

  const uint32_t beforeWrap = 0xFFFFFFFF - 1000;
  queueDelivery(weighInNamed("straddles"), beforeWrap);

  // 3 s later, 2 s of which are on the far side of the wrap.
  const uint32_t afterWrap = 2000;

  WeighIn next;
  uint32_t ageMs = 0;
  CHECK(nextDelivery(&next, &ageMs, afterWrap));
  CHECK(ageMs == 3001);
  CHECK(dropExpiredDeliveries(afterWrap) == 0);

  drainQueue();
}

int main() {
  deliversInTheOrderTheWeighInsWereTaken();
  keepsAWeighInThatWasNotAccepted();
  givesUpOnWeighInsHeldLongerThanADay();
  keepsAWeighInUntilItIsPastTheLimit();
  keepsTheNewestWeighInsWhenTheQueueFills();
  carriesAnAgeAcrossTheClockWrapping();

  if (failures > 0) {
    printf("\n%d check(s) failed\n", failures);
    return 1;
  }
  printf("\nall checks passed\n");
  return 0;
}
