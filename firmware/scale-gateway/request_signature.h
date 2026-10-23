#pragma once

#include <stddef.h>

// Proving a request came from this gateway.
//
// The board signs the request it is about to send with a key it shares with the
// server, and the server signs the request it received. They match only if the
// request arrived as it left and the sender held the key. The key itself never
// goes out on the wire, so listening to the network yields nothing that can be
// turned into a delivery of someone else's making.

// The header the signature travels in.
extern const char *const SIGNATURE_HEADER;

// What a signature needs room for: the digest's name, its 64 hex digits and a
// terminator.
static const size_t SIGNATURE_LENGTH = 7 + 64 + 1;

// Writes the signature for one request into `out`, which must hold
// SIGNATURE_LENGTH bytes. False means the board cannot compute the digest at
// all, which is a build without SHA-256 rather than anything provisioning can
// fix.
//
// The path is covered as well as the body, because a path carries meaning of its
// own: a ping names the gateway it speaks for there, and a server verifying the
// body alone would take a captured ping replayed at another gateway's path.
bool signRequest(const char *key, const char *method, const char *path, const char *body, char *out);
