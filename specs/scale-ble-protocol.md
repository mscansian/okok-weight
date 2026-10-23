# BLE Advertising Protocol — Bathroom Scale (`08:B8:D0:12:34:56`)

**Document status:** Reverse-engineered from advertisement capture. Weight formula validated against the scale's own display across two weigh-ins. All other fields as noted.

---

## 1. Device Overview

| Property | Value |
|---|---|
| Device type | BLE bathroom/body scale |
| Bluetooth address (MAC) | `B8:D0:12:34:56` (last 3 octets confirmed; full address as captured) |
| Communication model | Connectionless — data delivered entirely via advertising packets, no pairing/GATT connection required |
| Advertised profile | None (no standard Bluetooth SIG Weight Scale Service `0x181D`); fully proprietary payload |
| AD structure used | Manufacturer Specific Data (AD Type `0xFF`) |
| Declared "company ID" | `0xC0` marker byte — does not correspond to a registered Bluetooth SIG Company Identifier; vendor is not using this field per spec |
| Broadcast behavior | Continuous re-transmission of the current state at ~50–150 ms intervals while active |

---

## 2. Advertisement Packet Format

Each advertisement observed from this device consists of a single AD structure:

```
Offset  Size  Field           Value
0       1     Length          0x10 (16)
1       1     AD Type         0xFF (Manufacturer Specific Data)
2       15    Payload         see §3
```

Total over-the-air structure: 17 bytes.

---

## 3. Payload Format (15 bytes)

```
Offset  Size  Field              Example    Notes
0       1     Protocol marker    0xC0       Constant on every packet from this device
1       1     Sequence counter   varies     Rolls 0x00–0xFF; increments/decrements per retransmission
2       2     Field A (weight)   0x1C20     Big-endian uint16; see §4 for decoding
4       2     Field B            0x1770     Big-endian uint16; purpose unconfirmed, see §6
6       1     Constant           0x0A       Fixed on every packet observed
7       1     Constant           0x01       Fixed on every packet observed
8       1     Status flag        0x24/0x25  See §5
9       1     Constant           0x08       Fixed on every packet observed
10      5     Address tail       B8 D0 12 34 56
                                            Last five octets of the device's own address.
                                            The leading octet (0x08) is not transmitted, so
                                            the payload carries five bytes here, not six.
```

---

## 4. Weight Encoding (Field A, offset 2–3)

**Type:** unsigned 16-bit integer, big-endian.

**Formula:**
```
weight_kg = Field_A / 100
```

**Confirmed examples:**
```
Field A = 0x1C20 = 7200  ->  72.00 kg
Field A = 0x1BA8 = 7080  ->  70.80 kg   (matches the scale's own display)
```

**Resolution:** 0.01 kg per LSB.

**No offset.** An earlier reading of this field proposed `(Field_A − 200) / 100`, inferred
from a single reference weight. A later weigh-in read against the scale's own display
contradicts it: the raw field already carries kilograms times 100, and subtracting 200
made the result exactly 2.00 kg light.

**Validity window:** Field A only carries a meaningful reading while the status flag (offset 8) equals `0x25`. Outside that window it reads `0x0000` (see §5).

---

## 5. Status Flag (offset 8)

| Value | Meaning |
|---|---|
| `0x24` | No stable reading present — device idle, or a measurement was taken and has since been cleared |
| `0x25` | A stable, locked weight reading is currently present in Field A |

A receiver should discard/ignore Field A whenever the flag is `0x24`; only latch the value when the flag reads `0x25`.

---

## 6. Field B (offset 4–5) — Unconfirmed

**Type:** unsigned 16-bit integer, big-endian.

**Behavior:** stays at `0x0000` until the moment Field A locks (flag transitions to `0x25`), at which point it is set to `0x1770` = 6000 and — unlike Field A — is **not** cleared afterward; it persists through the idle state that follows.

**It has read exactly 6000 in every weigh-in observed so far, across separate sessions and
different weights.** Two things point away from it being a measurement at all: a measured
quantity would drift between sessions rather than repeat bit-for-bit, and 6000 is a round
number in decimal, which a sampled value has no reason to be. A session identifier is out
for the same reason — those would differ between sessions.

What fits is a constant: a fixed marker written alongside the locked reading, or a sentinel
standing in for a measurement this model does not take. Whether the value ever changes for
a different person or a different scale is untested.

This field is **not required** to obtain the weight reading and can be safely ignored by a minimal receiver implementation.

---

## 7. Session State Machine

Observed sequence of payload states over a single weigh-in, in chronological order:

| State | Field A | Field B | Flag | Behavior |
|---|---|---|---|---|
| 1. Idle | `0x0000` | `0x0000` | `0x24` | Platform unloaded |
| 2. Measuring | `0x1C20` | `0x0000` | `0x24` | Weight detected, not yet stable |
| 3. Locked | `0x1C20` | `0x1770` | `0x25` | Stable reading available — valid capture window |
| 4. Post-measurement idle | `0x0000` | `0x1770` | `0x24` | Field A cleared; Field B retained; device returning to idle/sleep |

Observed durations in the captured session: state 3 (the valid window) lasted approximately 2.3 seconds before reverting to state 4.

---

## 8. Reference Decode Table

| Raw payload (hex) | Field A | Field B | Flag | Decoded weight |
|---|---|---|---|---|
| `00 00 00 00 0A 01 24 08` | 0x0000 | 0x0000 | 0x24 | — (idle) |
| `1C 20 00 00 0A 01 24 08` | 0x1C20 | 0x0000 | 0x24 | — (measuring, not yet locked) |
| `1C 20 17 70 0A 01 25 08` | 0x1C20 | 0x1770 | 0x25 | **72.00 kg** |
| `00 00 17 70 0A 01 24 08` | 0x0000 | 0x1770 | 0x24 | — (post-measurement, Field A cleared) |

---

## 9. Confidence Summary

| Element | Confidence |
|---|---|
| Advertising-only, no-connection model | High — directly observed |
| Payload byte layout (marker, counter, MAC position) | High — consistent across all packets |
| Status flag semantics (0x24 idle / 0x25 locked) | High — perfectly correlated with Field A behavior |
| Weight formula `Field A / 100` | High — matches the scale's own display across two weigh-ins at different weights |
| Field B purpose | Unknown, but constant at 6000 across all observations; carries no per-weigh-in data |
| Constant bytes (`0x0A`, `0x01`, `0x08`) meaning | Unknown — invariant in this dataset, semantics not inferable from a single device/session |
