#include "scale_protocol.h"

#include <string.h>

// The BLE stack hands over the whole manufacturer-specific AD structure without
// splitting off a company identifier, so these are the offsets exactly as the
// protocol spec numbers them, counted from the 0xC0 marker.
static const size_t PAYLOAD_LENGTH = 15;
static const size_t OFFSET_MARKER = 0;
static const size_t OFFSET_SEQUENCE = 1;
static const size_t OFFSET_WEIGHT = 2;
static const size_t OFFSET_FIELD_B = 4;
static const size_t OFFSET_FLAG = 8;
static const size_t OFFSET_ADDRESS = 10;

static const uint8_t PROTOCOL_MARKER = 0xC0;
static const uint8_t FLAG_LOCKED = 0x25;

// The weight is carried as hundredths of a kilogram.
static const float WEIGHT_LSB_KG = 0.01f;

static uint16_t bigEndianAt(const uint8_t *payload, size_t offset) {
  return (uint16_t(payload[offset]) << 8) | payload[offset + 1];
}

bool decodeScalePayload(const uint8_t *payload, size_t length, ScaleReading *out) {
  if (length != PAYLOAD_LENGTH) {
    return false;
  }
  if (payload[OFFSET_MARKER] != PROTOCOL_MARKER) {
    return false;
  }
  // Outside the locked window the weight field reads zero, so a packet that is
  // not locked carries no reading to take.
  if (payload[OFFSET_FLAG] != FLAG_LOCKED) {
    return false;
  }

  out->weightKg = bigEndianAt(payload, OFFSET_WEIGHT) * WEIGHT_LSB_KG;
  out->fieldB = bigEndianAt(payload, OFFSET_FIELD_B);
  out->sequence = payload[OFFSET_SEQUENCE];
  memcpy(out->addressTail, payload + OFFSET_ADDRESS, SCALE_ADDRESS_TAIL_LENGTH);
  return true;
}

bool parseScaleAddressTail(const char *text, uint8_t *tail) {
  uint8_t octets[8];
  size_t count = 0;
  int value = -1;
  size_t length = strlen(text);

  for (size_t i = 0; i <= length; i++) {
    char character = i < length ? text[i] : ':';
    if (character == ':' || character == '-') {
      if (value < 0 || count >= sizeof(octets)) {
        return false;
      }
      octets[count++] = static_cast<uint8_t>(value);
      value = -1;
      continue;
    }

    int digit;
    if (character >= '0' && character <= '9') {
      digit = character - '0';
    } else if (character >= 'a' && character <= 'f') {
      digit = character - 'a' + 10;
    } else if (character >= 'A' && character <= 'F') {
      digit = character - 'A' + 10;
    } else {
      return false;
    }
    value = (value < 0 ? 0 : value) * 16 + digit;
    if (value > 0xFF) {
      return false;
    }
  }

  if (count < SCALE_ADDRESS_TAIL_LENGTH) {
    return false;
  }
  memcpy(tail, octets + (count - SCALE_ADDRESS_TAIL_LENGTH), SCALE_ADDRESS_TAIL_LENGTH);
  return true;
}
