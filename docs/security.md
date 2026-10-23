# Security

## Who is allowed to post a weigh-in

Every request from the gateway has an `X-Signature` header. It is an
HMAC-SHA-256 of the method, the path and the exact body bytes, joined by
newlines, under a key the gateway and the server share. The server computes the
same signature over what it received and compares the two. If a request is
unsigned, changed in transit or signed with another key, the server answers
`401` and stores nothing.

The signature covers the path because a ping names its gateway in the path. If
only the body were signed, someone could replay a captured ping at any address
they chose and keep a board that never existed looking alive.

The key never goes over the network, so someone listening on it can't use what
they see to forge a delivery. They can replay a captured request at the path it
was signed for, and nothing here stops that. Replaying a weigh-in does no harm:
it carries the `event_id` of the original, so the server returns the reading it
already has instead of storing a second one. Replaying a ping does some harm,
because it keeps a gateway looking alive after it has stopped. The usual fix is
a signed timestamp checked against a time window, and this gateway can't do that
because it has no clock.

The signature proves a request came from the gateway. It doesn't hide the
weight: over `http://` anyone on the path can read the body. Anyone within a few
metres of the scale can also read the BLE advertisement the weight came from. An
`https://` url encrypts the network half. The signature also doesn't protect
`GET /api/v1/readings`, which anyone who can reach the port can read.

With `https://` you get encryption but not identity, because the gateway accepts
the certificate without verifying it. Verifying it would need a chain walk and a
public-key operation on top of the record buffers, and the heap has no room for
that after BLE and WiFi. The signature makes that loss acceptable. The key is
never sent, so an impostor server can't learn it from a delivery, and can't add
a weigh-in to the history without it. What an impostor can do is pretend a
delivery arrived, so the gateway drops that weigh-in from its queue. Someone in
the path can make you lose readings, but can't invent them.

A board with no key says so at boot and doesn't post, because the server would
reject every delivery. Weigh-ins taken in the meantime queue the same as during
any outage, and go out once you set a key.

## What is not protected

The read routes have no authentication, so anyone with the URL can browse your
weight history on the weight page. `PUT /api/v1/devices/{address}` has no
authentication either. The page doesn't create that hole, but it makes it easy
to find. Put auth in front of the server before you give it a public hostname.

## Where the secrets live

The WiFi passphrase and the signing key are stored in the ESP32's NVS
partition. They are written over serial and are never part of a build. NVS is
not encrypted, so anyone who can read the flash (in person, over USB, with
`esptool read_flash`) can recover both. Protecting against that needs flash
encryption burned into eFuse, which can't be undone on that board.
