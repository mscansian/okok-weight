#pragma once

#include <stdint.h>

// How this board has come up, counted rather than reported one at a time. The
// reason for the last reset says a brownout happened; only a tally says how
// often, which is the difference between knowing a supply is marginal and
// knowing whether anything done about it helped.
//
// The tally lives in the RTC domain, which a reset leaves alone and a power cut
// clears — so it counts from the last time the board was unplugged rather than
// from the day it was made. That costs it nothing in flash, which is what earns
// it the domain: the moment a boot would write to flash is the moment the supply
// is least able to see the write through. The longer history is the server's,
// built from the pings it keeps.
namespace bootTally {

// Reads what the run before this one left behind, and counts this boot into it.
// Nothing else here means anything until it has run.
void recordBoot();

// Every time this board has come up since it was last unplugged.
uint32_t boots();

// How many of those the brownout detector caused.
uint32_t brownouts();

// Keeps the length of the run in progress where the next boot can read it. A run
// only ever knows its own length, so leaving it behind is the only way the board
// that comes up next can say how long the board before it lasted.
void recordUptime();

// How long the run before this one lasted. False when there is nothing to read:
// a supply that failed hard enough to clear the RTC domain leaves a boot looking
// like its first.
bool knowsPreviousUptime();
uint32_t previousUptimeMs();

}  // namespace bootTally
