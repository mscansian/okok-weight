#pragma once

#include <esp_wifi_types.h>

// Network settings are provisioned over the serial console and kept in NVS, so
// nothing here is a secret and nothing here is per-installation.

// How many scales one gateway may be told to listen for. Each costs a stored
// address, a tail compared against every advertisement heard, and room to build
// a weigh-in in. A gateway hears what is in radio range of wherever it was put,
// which is a room and not a building, so this is a small number by nature.
#define MAX_SCALES 4

// The scale repeats one locked reading every 50-150 ms and keeps repeating it
// for roughly 2.3 s. A listen of 150 ms therefore spans the widest gap between
// two repeats, so any listen that opens while the scale is broadcasting hears
// it. That makes the window a property of the scale rather than something to
// tune, and the interval the only one of the two worth setting.
#define SCAN_WINDOW_MS 150
#define LOCKED_BROADCAST_MS 2300

// How often the receiver listens while nothing is happening — which is nearly
// all the time, and so is where the radio's heat comes from. Missing the first
// repeat of a weigh-in costs nothing: the event id is minted on whichever lock
// arrives first and the weight handed on is the last one, so listening less
// often only joins a weigh-in later, never reports a different one.
#define IDLE_SCAN_INTERVAL_MS 1000

// Once a lock has arrived the receiver listens without a meaningful break,
// because the end of a weigh-in is the part that must not be sampled: the
// weight handed on is the last lock of it. The gap is the one the firmware has
// always run with.
#define WEIGH_IN_SCAN_INTERVAL_MS (SCAN_WINDOW_MS + 1)

// A weigh-in the receiver never listened through is worse than one it joined
// late, so the idle rhythm has to fit inside a broadcast rather than step over
// one.
static_assert(
  IDLE_SCAN_INTERVAL_MS + SCAN_WINDOW_MS <= LOCKED_BROADCAST_MS,
  "the receiver could go a whole broadcast without listening and miss the weigh-in"
);

// The scale locks more than once while someone settles onto it, each lock
// carrying a different weight, and the reading that matters is the last one. A
// weigh-in is handed on once locked packets have stopped arriving for this long,
// which has to outlast the gap between two listens by enough that two unlucky
// listens in a row cannot end a weigh-in early.
#define WEIGH_IN_SETTLE_MS 1500

static_assert(
  WEIGH_IN_SETTLE_MS >= 3 * WEIGH_IN_SCAN_INTERVAL_MS,
  "a weigh-in could settle between two listens, losing the locks that follow it"
);

// How much of the time between the access point's beacons the WiFi radio may
// spend parked. The board only ever speaks first — it listens on no port — so
// what waits on a beacon is the answer to a request rather than a request; and a
// delivery says how long ago its weigh-in was taken rather than when, so a
// slower round trip moves nothing in the history. What it buys back is the
// receive current of a radio that is otherwise never off.
//
// The radio wakes for every beacon, which is the shallower of the two modes it
// offers. It draws more across an hour than sleeping through beacons would, and
// in exchange the step from parked to receiving is a smaller one each time it is
// taken — which is the half of the trade that matters on a board whose rail is
// held up by its own regulator and whatever cable reaches it.
#define WIFI_POWER_SAVE WIFI_PS_MIN_MODEM

// The clock the gateway runs at for its whole life. Nothing it does between
// weigh-ins is compute-bound — the loop spends its time waiting on a radio — so
// the cycles above this one go into idling, and idling all day is what warms the
// regulator.
//
// Two separate things put the floor at 80 MHz: it is the slowest clock the WiFi
// and BLE radios run at, and it is the slowest one that still leaves the
// peripheral bus at 80 MHz, so the console's baud rate and the timers keep the
// divisors they were given. Below it, both of those stop holding at once.
//
// The TLS handshake is the only arithmetic here long enough to feel the
// difference. It is bounded by a timeout with room to spare, and every ping
// reports how long its own round trip took.
#define CPU_FREQUENCY_MHZ 80

// How long the scan waits for the link before starting anyway. Joining a
// network and bringing up the BLE controller are the two largest draws the
// board ever makes, and starting the second while the first is still going
// makes one demand of the supply out of two.
//
// It gives up waiting rather than never scanning: a gateway with no network to
// join still hears weigh-ins and still holds them, and the queue is what that
// is for. Long enough for a join that is going to succeed, short enough that a
// board with a wrong passphrase is scanning before anyone has walked away.
#define WIFI_SETTLE_TIMEOUT_MS 8000

// Reported on every ping, so two boards behaving differently can be told apart
// without a cable and a console.
#define FIRMWARE_VERSION "0.1.0"

// How often the gateway reports that it is alive. The ping carries this number,
// and the server measures the board's silence against it rather than against a
// threshold of its own — so changing it here is the whole of changing it.
#define PING_INTERVAL_MS 300000
