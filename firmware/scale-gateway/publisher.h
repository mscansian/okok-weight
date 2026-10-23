#pragma once

#include <stddef.h>

#include "scale_scanner.h"

// Takes a settled weigh-in. It goes to the server straight away when there is a
// link, and waits its turn when there is not.
void publish(const WeighIn &weighIn);

// Delivers what is waiting, and gives up on whatever has waited too long. Cheap
// enough to call from the main loop, which is where the retries come from.
void deliverPending();

// How many weigh-ins the server has still to be told about.
size_t heldWeighIns();

// Reports to the server that this gateway is alive, and how it is getting on.
// It takes the route a weigh-in takes as far as the network goes, so a ping that
// answers also means the next real reading has somewhere to land.
void pingServer();

// Remembers that the server is owed a ping, which is how both a fresh url and a
// fresh connection come to be reported without either having to wait out the
// interval.
void owePing();

// Pings when one is owed or the interval has run out and there is a link to do
// it over. Cheap enough to call from the main loop.
void pingIfDue();
