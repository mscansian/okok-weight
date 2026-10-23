# Security

## Who is allowed to post a weigh-in

Every request the gateway makes carries an `X-Signature` header: HMAC-SHA-256,
under a key the gateway and the server share, over the method, the path and the
exact bytes of the body, joined by newlines. The server signs what it received
and compares. Anything unsigned, altered in flight, or signed with another key
comes back `401` and is not stored.

The path is covered because it carries meaning the body does not — a ping names
the gateway it speaks for there, and a signature over the body alone would let a
captured ping be replayed at any address the replayer chose, keeping a board that
was never there looking alive.

The key never goes on the wire, so a network someone else can listen on gives
them nothing they can turn into a delivery of their own. What they *can* do is
replay a capture at the path it was signed for, and nothing here stops that. For
a weigh-in it costs nothing: the replay carries the `event_id` of the reading it
already made and comes back as that reading rather than a second one. For a ping
it does cost something, since a replay keeps a gateway looking alive after it has
stopped. The usual defence, a signed timestamp held to a window, is the one this
gateway cannot mount: it has no clock to read.

The signature covers the body and nothing else. Two things it therefore does not
do. It does not hide the weight — over `http://` the body travels in the clear,
though so does the BLE advertisement it came from, to anyone within a few metres
of the scale. An `https://` url closes that half. And it does not gate
`GET /api/v1/readings`, which is open to whoever can reach the port.

What `https://` buys here is the encryption and not the identity: the
certificate is accepted without being verified. Verifying it is what this board
cannot afford — a chain walk and a public-key operation on top of the record
buffers, out of a heap BLE and WiFi have already been paid from — and the
signature is what makes that affordable to give up. The key is never sent, so an
impostor cannot learn it from a delivery and cannot forge a weigh-in into the
history without it. What an impostor can do is answer as though a delivery
landed, which drops that weigh-in from the queue. The trade is therefore
readings lost to someone in the path, not readings invented by them.

A board with no key stored says so at boot and refuses to post rather than
sending deliveries the server will only reject. Weigh-ins taken meanwhile queue
as they do over any other outage, and go out once a key is set.

## What is not protected

The read routes have no authentication, so the weight page makes the weight
history browsable to anyone who has the URL. And `PUT /api/v1/devices/{address}`
is unauthenticated too — the page is not what opens that, but it is what makes
it obvious. Put auth in front of the server before it goes on a public hostname.

## Where the secrets live

The passphrase and the signing key live in the ESP32's NVS partition, written
over serial and never part of a build. NVS is not encrypted, so anyone who can
read the flash — physically, over USB, with `esptool read_flash` — can recover
both. Guarding against that needs flash encryption burned into eFuse, which is a
one-way change to the board.
