#pragma once

#include <Arduino.h>

// Starts listening for disconnect events, which is the only place the reason
// for one is ever reported.
void beginWiFi();

// Applies the stored network settings. It reports nothing, because what happens
// next arrives asynchronously; describeDisconnect is where the outcome is read.
void connectWiFi();

void disconnectWiFi();

// Waits for the link, and reports whether it arrived before the wait ran out.
// Nothing needs the link to be up to carry on, so the answer is worth reporting
// and not worth acting on.
bool awaitWiFi(uint32_t timeoutMs);

bool isWiFiConnected();

// The address the network handed this board, which is what a console needs to
// reach it.
String wifiAddress();

// The address of the board's own radio: the one name it has that it was not
// given, and the name the server knows this gateway by. Readable whether or not
// the link is up.
String hardwareAddress();

// The network the link is actually on, which is not quite the one that was
// provisioned until the board has joined it. Empty while it has not.
String currentNetwork();

// True once each time the link comes up, and false until it comes up again.
// Announcing the address and owing the server a ping both hang off that edge.
bool wifiJustConnected();

// The last disconnect reason, in words rather than in the SDK's vocabulary.
String describeDisconnect();

// How many times the link has dropped since this board came up, and the SDK's
// own number for why it last did. A radio that sleeps deeply between beacons
// buys its power back by hearing fewer of them, and a count that climbs is how
// that shows up before anything else does: every reconnection owes the server a
// ping, so churn here becomes traffic there.
uint32_t disconnectCount();
uint8_t disconnectReason();
