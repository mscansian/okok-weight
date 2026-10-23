// Exercises the decoder against the reference payloads in §4 and §8 of
// specs/scale-ble-protocol.md. Builds on the host: `make firmware-test`.

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "scale_protocol.h"

static int failures = 0;

static void checkThat(bool passed, const char *expression, int line) {
  if (!passed) {
    printf("  FAIL line %d: %s\n", line, expression);
    failures++;
  }
}

#define CHECK(expression) checkThat((expression), #expression, __LINE__)

static bool nearly(float actual, float expected) {
  return fabsf(actual - expected) < 0.001f;
}

// The scale this repo was reverse-engineered against.
static const uint8_t SCALE_TAIL[SCALE_ADDRESS_TAIL_LENGTH] = {0xB8, 0xD0, 0x12, 0x34, 0x56};

// §8 tabulates offsets 2 through 9 — the two data fields, the three constants
// and the status flag. A whole packet is the marker and sequence counter ahead
// of them and the address tail behind.
static void buildPayload(uint8_t *payload, uint8_t sequence, const uint8_t body[8], const uint8_t *tail) {
  payload[0] = 0xC0;
  payload[1] = sequence;
  memcpy(payload + 2, body, 8);
  memcpy(payload + 10, tail, SCALE_ADDRESS_TAIL_LENGTH);
}

static void decodesTheLockedReadingsFromTheSpec() {
  printf("decodes the locked readings from the spec\n");

  // §8: 72.00 kg, the row whose weight §4 checked against the scale's display.
  const uint8_t locked[8] = {0x1C, 0x20, 0x17, 0x70, 0x0A, 0x01, 0x25, 0x08};
  uint8_t payload[15];
  buildPayload(payload, 39, locked, SCALE_TAIL);

  ScaleReading reading;
  CHECK(decodeScalePayload(payload, sizeof(payload), &reading));
  CHECK(nearly(reading.weightKg, 72.00f));
  CHECK(reading.fieldB == 6000);
  CHECK(reading.sequence == 39);
  CHECK(memcmp(reading.addressTail, SCALE_TAIL, SCALE_ADDRESS_TAIL_LENGTH) == 0);

  // §4: the second weigh-in read against the display, 0x1BA8 = 70.80 kg. It is
  // the reading that ruled out the earlier (Field_A - 200) / 100 formula, so a
  // decoder that reintroduced the offset would fail here and nowhere else.
  const uint8_t other[8] = {0x1B, 0xA8, 0x17, 0x70, 0x0A, 0x01, 0x25, 0x08};
  buildPayload(payload, 40, other, SCALE_TAIL);

  CHECK(decodeScalePayload(payload, sizeof(payload), &reading));
  CHECK(nearly(reading.weightKg, 70.80f));
}

static void ignoresEveryPacketThatIsNotALockedReading() {
  printf("ignores every packet that is not a locked reading\n");

  ScaleReading reading;
  uint8_t payload[15];

  // §7 state 1: idle, platform unloaded.
  const uint8_t idle[8] = {0x00, 0x00, 0x00, 0x00, 0x0A, 0x01, 0x24, 0x08};
  buildPayload(payload, 1, idle, SCALE_TAIL);
  CHECK(!decodeScalePayload(payload, sizeof(payload), &reading));

  // §7 state 2: a weight is present but has not settled. Taking this one would
  // record whatever the scale read on the way down.
  const uint8_t measuring[8] = {0x1C, 0x20, 0x00, 0x00, 0x0A, 0x01, 0x24, 0x08};
  buildPayload(payload, 2, measuring, SCALE_TAIL);
  CHECK(!decodeScalePayload(payload, sizeof(payload), &reading));

  // §7 state 4: the weight has been cleared, field B has not.
  const uint8_t cleared[8] = {0x00, 0x00, 0x17, 0x70, 0x0A, 0x01, 0x24, 0x08};
  buildPayload(payload, 3, cleared, SCALE_TAIL);
  CHECK(!decodeScalePayload(payload, sizeof(payload), &reading));
}

static void saysWhichScaleAReadingCameFrom() {
  printf("says which scale a reading came from\n");

  ScaleReading reading;
  uint8_t payload[15];
  const uint8_t locked[8] = {0x1C, 0x20, 0x17, 0x70, 0x0A, 0x01, 0x25, 0x08};

  // Another scale of the same model, broadcasting a perfectly valid reading. It
  // decodes like any other, and the tail is what tells the two apart — a gateway
  // listens for several scales and only its settings know which.
  const uint8_t neighbour[SCALE_ADDRESS_TAIL_LENGTH] = {0xB8, 0xD0, 0x12, 0x34, 0x57};
  buildPayload(payload, 4, locked, neighbour);
  CHECK(decodeScalePayload(payload, sizeof(payload), &reading));
  CHECK(memcmp(reading.addressTail, neighbour, SCALE_ADDRESS_TAIL_LENGTH) == 0);
  CHECK(memcmp(reading.addressTail, SCALE_TAIL, SCALE_ADDRESS_TAIL_LENGTH) != 0);
}

static void ignoresPacketsFromAnythingElse() {
  printf("ignores packets from anything else\n");

  ScaleReading reading;
  uint8_t payload[15];
  const uint8_t locked[8] = {0x1C, 0x20, 0x17, 0x70, 0x0A, 0x01, 0x25, 0x08};

  // A device using manufacturer data of the same length but not this protocol.
  buildPayload(payload, 5, locked, SCALE_TAIL);
  payload[0] = 0x4C;
  CHECK(!decodeScalePayload(payload, sizeof(payload), &reading));

  // A short payload must be turned away before any offset is read.
  buildPayload(payload, 6, locked, SCALE_TAIL);
  CHECK(!decodeScalePayload(payload, 9, &reading));
}

static void readsAddressesTheWayPeopleWriteThem() {
  printf("reads addresses the way people write them\n");

  uint8_t tail[SCALE_ADDRESS_TAIL_LENGTH];

  CHECK(parseScaleAddressTail("08:B8:D0:12:34:56", tail));
  CHECK(memcmp(tail, SCALE_TAIL, sizeof(tail)) == 0);

  CHECK(parseScaleAddressTail("08-b8-d0-12-34-56", tail));
  CHECK(memcmp(tail, SCALE_TAIL, sizeof(tail)) == 0);

  // The tail alone is what the packet carries, so it is accepted as written.
  CHECK(parseScaleAddressTail("B8:D0:12:34:56", tail));
  CHECK(memcmp(tail, SCALE_TAIL, sizeof(tail)) == 0);

  CHECK(!parseScaleAddressTail("", tail));
  CHECK(!parseScaleAddressTail("12:34:56", tail));
  CHECK(!parseScaleAddressTail("08:B8:D0:12:34:5G", tail));
  CHECK(!parseScaleAddressTail("08:B8:D0:12:34:156", tail));
}

int main() {
  decodesTheLockedReadingsFromTheSpec();
  ignoresEveryPacketThatIsNotALockedReading();
  saysWhichScaleAReadingCameFrom();
  ignoresPacketsFromAnythingElse();
  readsAddressesTheWayPeopleWriteThem();

  if (failures > 0) {
    printf("\n%d check(s) failed\n", failures);
    return 1;
  }
  printf("\nall checks passed\n");
  return 0;
}
