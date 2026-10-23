# okok-weight

A BLE bathroom scale that broadcasts its readings in the clear, an ESP32 that
listens for them, and a server that stores them. The scale never accepts a
connection, so the gateway is a passive receiver — it scans and never transmits.

## Quickstart

```
make install        # once, installs the pinned ESP32 core
make upload
make monitor
make firmware-test  # decodes the spec's reference payloads, no board needed
```

`make upload` reads the attached chip and flashes for it, so an ESP32 and an
ESP32-S3 both take the same command. `make compile` has no board to ask and
builds for the plain ESP32; `make compile BOARD=esp32s3` targets the other.

## Provisioning

The firmware carries no network settings, so the same image runs on any board.
Settings are pushed over serial into NVS, where they survive reboots *and*
reflashes — you provision once, not once per `make upload`.

`make provision` takes these from the environment:

| Variable | Required | Example | What it is |
|---|---|---|---|
| `WIFI_SSID` | yes | `your-network` | the network the gateway joins |
| `WIFI_PASSWORD` | yes, but **leave unset** | — | its passphrase — see below |
| `SERVER_URL` | yes | `https://scale.example.com` | where the server is, without a route on the end — the gateway posts weigh-ins and pings to different ones. Must be reachable *from the gateway* — never `localhost`. `https://` encrypts the body without verifying the certificate, `http://` suits a server on the LAN |
| `SCALE_GATEWAY_KEY` | yes, but **leave unset** | — | the key deliveries are signed with — see below |
| `SCALE_ADDRESSES` | yes | `AA:BB:CC:DD:EE:FF` | which scales to listen for, up to four separated by spaces. Full MAC or just its last five octets; colons or dashes, any case |

```
export WIFI_SSID='your-network'
export SERVER_URL='https://scale.example.com'
export SCALE_ADDRESSES='AA:BB:CC:DD:EE:FF 08:B8:D0:12:34:56'
make provision
```

One gateway serves every scale in radio range of it, and a weigh-in carries the
address of the scale it was taken on, so two scales through one board stay two
histories. A scale that is not on the list is ignored — including another of the
same model next door, which broadcasts a perfectly valid reading that this board
has no business recording.

Anything left unset is prompted for instead. **Leave `WIFI_PASSWORD` and
`SCALE_GATEWAY_KEY` unset on purpose:** their prompts do not echo, so neither
secret enters your shell history, lands in a file, or reaches a command line.
Exporting them works, but then they live in your history and in whatever file
exported them.

`SCALE_GATEWAY_KEY` is the one secret both halves need — the server verifies
with the same value, under the same name. Generate one with
`openssl rand -hex 32`, and keep it somewhere you can paste it from twice.

`make show` prints what is stored, withholding both secrets. `make ping` makes
the board report to the server now and prints the round trip; it does that on its
own every five minutes anyway, and immediately whenever the link comes up or the
url changes.

The same settings can be typed by hand over `make monitor`: `network`,
`passphrase`, `url`, `key`, `scale`, `ping`, `show`, `connect`, `forget`. Everything
after the command word is taken verbatim, so spaces need no quoting.

## Keeping the board cool enough to stay up

The gateway holds no state it cannot rebuild, so the cost of a reset is the
weigh-ins still queued — but a board that resets because its supply sagged does
it again a few hours later. The firmware's part in that is the current it draws
while doing nothing, because that is what heats the regulator all day:

| Setting | What it trades |
|---|---|
| `WIFI_POWER_SAVE` | how much of the time between the access point's beacons the WiFi radio spends parked. Waking for each beacon costs the most current of the modes that park at all, and makes the step out of the parked state the smallest; sleeping through beacons inverts both, and adds up to the listen interval to the inbound half of a POST, which `age_ms` keeps out of the history entirely |
| `CPU_FREQUENCY_MHZ` | the clock the board runs at, which it holds for its whole life. Nothing between weigh-ins is compute-bound, so what this costs is arithmetic: the TLS handshake, whose round trip every ping reports. 80 MHz is the floor — below it the radios stop running and the peripheral bus stops being 80 MHz, so the console's baud rate and the timers lose their divisors |
| `IDLE_SCAN_INTERVAL_MS` | how often the BLE receiver listens while nobody is on the scale, which is nearly all the time. Costs joining a weigh-in a little after it starts. It cannot cost a *different* weight: the receiver goes continuous the moment it hears a lock, so the end of a weigh-in — the part the reported weight comes from — is never sampled |

Both scan constants are held to the scale's own behaviour by `static_assert`, so
listening too rarely to catch a broadcast, or declaring a weigh-in settled
between two listens, is a build error rather than a quietly light reading.

Firmware is only half of it. A brownout means the 3V3 rail went below roughly
2.43 V, which is well under the voltage the ESP32 needs, so the regulator is
collapsing rather than merely drooping — usually a dev board's own LDO with a
long USB cable and no bulk capacitance behind it. Feeding 3V3 directly from a
supply with headroom fixes more than any of the above.

`GET /api/v1/devices` is where you find out whether any of it worked: `brownouts`
against `boots`. The `locks` on a recorded weigh-in is the other half of the
answer — it says whether the receiver is still hearing weigh-ins whole.

## When the link goes down

A weigh-in taken while the network is down is held rather than lost. The gateway
queues it, retries every 30 s, and keeps trying for 24 hours before giving up on
it — long enough to cover a router reboot or a night of ISP trouble, short enough
that nothing lands in the history a day stale. Up to 64 weigh-ins wait at once;
past that the oldest goes to make room. `make show` reports how many are waiting.

Each delivery says how long ago the weigh-in was taken rather than when, and the
server subtracts that from the arrival to time it. This is why: the ESP32's clock
is not battery-backed, so a board that has just come up from a power cut does not
know the date, and if the network is still down it has no way to ask. It can
always answer how long ago something happened, from the moment it boots.

The queue lives in RAM, so a weigh-in still waiting when the board resets is
gone. That trades a rarer failure — the board resetting mid-outage — for not
writing to flash on every weigh-in, and for not having to timestamp readings the
board took before it knew the time.

## When the link is up and the server is still unreachable

A TLS session costs this board two 16 KB record buffers before a byte of it is
about anything, and BLE and WiFi are holding their own memory the whole time. So
https is where the gateway runs out of heap first, and it does not run out in a
way that says so — an allocation that fails inside mbedTLS surfaces as a refused
connection:

```
tls: X509 - A fatal error occurred, eg the chain is too long or the vrfy callback failed
heap: 60020 free, largest block 34804, going in
ping: … failed, status -1 (connection refused)
```

The second number is the one that matters. Memory in pieces too small to use
reads as free in every other measure, and a POST needs its share in one piece.
Every ping carries `free_heap` and `largest_free_block` too, so the trend is
readable from `GET /api/v1/devices` without a cable — though a ping that lands is a
ping that found the room, which makes the console the place to watch a board
that has stopped.

Two things buy the headroom. The Bluetooth Classic half of the controller goes
back to the heap before BLE starts, this gateway being a scanner that never uses
it. And the certificate chain is not verified, which is the larger saving and is
covered under posting a weigh-in below.

## The server

A FastAPI service that takes the gateway's weigh-ins and keeps them in SQLite,
along with what each gateway last said about itself. Only the weight and the
arrival time are stored of a weigh-in — the scale has no clock, and the rest of
the advertisement describes reception rather than the weigh-in, so it goes to the
logs instead.

Run it on the machine named in `url`:

```
make server-up     # container, database in server/data/ on this host
make server-down
```

Or straight from the checkout, without Docker:

```
make server        # or: SCALE_DB_PATH=... uv run --project server scale-server
make server-test
```

Step on the scale, then ask for the history:

```
curl localhost:8080/api/v1/readings
```

```json
{"items": [{"id": "bab113ca-…", "event_id": "3aece795-…", "address": "08:b8:d0:12:34:56",
            "weight_kg": 72.35,
            "measured_at": "2026-08-29T19:47:13.541460Z",
            "recorded_at": "2026-08-29T19:47:13.541460Z"}],
 "total": 1, "limit": 50, "offset": 0}
```

| Route | What it does |
|---|---|
| `POST /api/v1/readings` | takes one weigh-in |
| `GET /api/v1/readings` | the history, newest first, `?address=&limit=&offset=` |
| `POST /api/v1/devices/{address}/ping` | takes a gateway's report that it is alive |
| `GET /api/v1/devices` | every gateway that has ever pinged |
| `GET /api/v1/devices/{address}` | one of them |
| `PUT /api/v1/devices/{address}` | sets what that board is called |
| `GET /api/v1/health` | answers only once SQLite has |

Every route sits under `/api/v1`, and the gateway has that prefix compiled in
rather than reading it off the stored url — so `url` stays the host the server
is on, and a board is moved to a later version of the API by reflashing it.

Interactive docs are at `/docs`.

### The pages

`/` is a weight monitor: the last weigh-in at the size the scale would show it,
what it did since the one before, a line through the readings over time, and the
history under it. Hovering a date in the history says exactly when the weigh-in
happened and, if the gateway had to hold it, how long it waited.

`/devices` is the other half, and reads differently on purpose: one row per
board, every field spelled as the API spells it, the human reading beside the
raw one. It is the view `make show` gives over a cable, for boards that do their
job without one. The name is the only field it can set, because it is the only
column a ping does not rewrite — click it and type.

The weight page also asks for the gateways, but only to say whether anything is
still reporting: a board gone quiet means the newest weigh-in here is older than
the newest weigh-in taken, which is the one thing about the hardware that
belongs next to the weight.

Neither page holds state or a copy of the data. Both read the same JSON routes
above, so nothing is visible on them that a `curl` cannot also get. They look
again every half minute, and whenever you come back to the tab.

Two things worth knowing before it goes on a public hostname. The read routes
have no authentication, so the page makes the weight history browsable to
anyone who has the URL. And `PUT /api/v1/devices/{address}` is unauthenticated
too — the page is not what opens that, but it is what makes it obvious.

### Several gateways, one endpoint

Every reading carries the `address` of the scale it came from, so any number of
gateways can post here and each scale's history is `GET /api/v1/readings?address=…`.
One thing that does not follow: two gateways in range of the same scale each
name the same weigh-in for themselves, so it lands twice. Nothing here can tell
those apart, though `GET /api/v1/devices` will show both boards listing that
scale in `scale_addresses`, which is the shape of the problem if not a fix for it.

### Delivering a weigh-in twice

The gateway names each weigh-in with an `event_id` it mints when the scale first
locks — a UUID drawn from the ESP32's hardware entropy source — and it repeats
that id if it ever repeats the delivery. A repeat is answered `200` with
the reading the first delivery produced, rather than `201` and a second weigh-in.
This is what makes the gateway's retries safe: a POST that arrived but whose
answer did not comes back as the reading it already made.

`measured_at` is when the weigh-in happened and `recorded_at` is when it reached
the server. They agree on a weigh-in delivered as it was taken, and differ by
however long the gateway had to hold one back. Readings are ordered and
paginated by `measured_at`, so a late delivery lands in its own place in the
history rather than at the top of it.

### Is the gateway alive?

Every five minutes the gateway posts to `/api/v1/devices/{address}/ping`, where the
address is the one belonging to its own radio — the single name it has that
nobody had to give it. The first ping a board ever sends is what puts it in the
database; nothing has to be registered first, because the signing key is what
says a ping is genuine.

```
curl localhost:8080/api/v1/devices
```

```json
{"items": [{"address": "a0:b7:65:12:34:56", "name": null, "online": true,
            "first_seen_at": "2026-08-28T09:14:02.113204Z",
            "last_seen_at": "2026-08-29T19:45:11.882015Z",
            "client_ip": "203.0.113.7",
            "scale_addresses": ["08:b8:d0:12:34:56"], "ssid": "your-network",
            "wifi_rssi": -58, "queued": 0, "uptime_ms": 124580000,
            "interval_ms": 300000, "firmware_version": "0.1.0",
            "reset_reason": "power-on", "boots": 12, "brownouts": 0,
            "free_heap": 104312, "largest_free_block": 51188}],
 "total": 1}
```

`online` is worked out as the answer is written and never stored, so a server
that was not running leaves nothing behind still claiming to be up. A gateway is
online until it has been quiet for longer than the run of pings it is allowed to
miss — five of them by default, and at the firmware's five-minute interval that
is half an hour of silence before a board is called dead.

The interval is the one the *gateway* declares rather than a number the server
holds, so a board given a new cadence is judged by that cadence from its next
ping. Changing `PING_INTERVAL_MS` in the firmware is the whole of changing it.

`name` is yours to set and the only column a ping leaves alone — a board knows
its own address and nothing whatever about where it was put:

```
curl -X PUT localhost:8080/api/v1/devices/a0:b7:65:12:34:56 \
  -H 'Content-Type: application/json' -d '{"name": "bathroom"}'
```

`null` takes the name away again. The board has to have pinged at least once,
because a ping is what brings its row into being.

#### What a ping carries, and why

Liveness is the small part of it. The rest is what the serial console would tell
you if a cable were plugged in, and a board doing its job is a board that has
none.

| Field | What it is worth |
|---|---|
| `scale_addresses` | which scales this board was told to listen for. A gateway aimed at the wrong scale and a gateway nobody has stood on are the same silence in the reading history; this is what tells them apart. Empty is a board told none; absent is a board too old to have been told more than one |
| `queued` | weigh-ins taken but not delivered. Non-zero says the history is behind what the scale has actually seen |
| `uptime_ms` | a board that answers every ping but whose uptime keeps returning to zero is rebooting, which liveness alone would never show |
| `reset_reason` | why it last came up. A brownout is a power supply to fix and a panic is firmware to fix |
| `boots`, `brownouts` | every time this board has come up, and how many of those the brownout detector caused. The reason above names the last boot only, which for a supply that sags now and then says that it happened but never how often. These two are counted in the board's own storage and survive reboots and reflashes, so the rate reads off `GET /api/v1/devices` rather than out of the logs |
| `free_heap`, `largest_free_block` | what the board had left to allocate as it built the ping. A TLS session wants two 16 KB record buffers in one piece each, so a heap that has fragmented into small pieces costs the board the server while the total still looks healthy. The second number is the one that runs out first |
| `ssid`, `wifi_rssi` | which network it joined and how well it is hearing it |
| `firmware_version` | which build is on which board, for when two of them behave differently |

Two of these say what a board was *told*, not what it found: `scale_addresses`
is the setting as it was provisioned, so each entry may be the five octets the
advertisement carries rather than a whole address. `client_ip` is the opposite —
the gateway cannot know the address its traffic leaves the network with, so the
server writes down where the ping arrived from.

Behind a reverse proxy, where the ping arrived from is the proxy: every request
reaches the server from the same address, so the peer address names the proxy
and one rate-limit allowance is shared out among everyone behind it — which is
one busy caller away from spending the gateway's. So the caller is looked for in
`CF-Connecting-IP`, then `X-Real-IP`, then the head of `X-Forwarded-For`, and
the peer address only when none of them is there.

The order is not arbitrary. nginx sets `X-Real-IP` to the address it was called
from, which behind Cloudflare is Cloudflare's — read before the CDN's own
header, it would name an edge as the visitor.

Any of these can be made up by whoever sends the request, and that is accepted
rather than overlooked. The address feeds a rate limit and a log line; nothing
is admitted on the strength of it, because a weigh-in and a ping get in by their
signature whatever address they arrive from. A value that is not an address is
logged and passed over.

### Configuration

| Variable | Required | Default | What it is |
|---|---|---|---|
| `SCALE_DB_PATH` | yes | — | where the SQLite file lives. No default on purpose: a fallback would put the history somewhere you did not choose |
| `SCALE_GATEWAY_KEY` | yes | — | the key deliveries are signed with, the same one the gateway was provisioned with. At least 32 characters |
| `SCALE_HOST` | no | `0.0.0.0` | interface to bind |
| `SCALE_PORT` | no | `8080` | port to bind |
| `SCALE_LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `SCALE_MAX_BODY_BYTES` | no | `4096` | bodies above this are refused unread |
| `SCALE_RATE_LIMIT_PER_MINUTE` | no | `120` | per-client ceiling |
| `SCALE_DEFAULT_PAGE_SIZE` | no | `50` | page size when `limit` is omitted |
| `SCALE_MAX_PAGE_SIZE` | no | `500` | largest `limit` accepted |
| `SCALE_DEVICE_OFFLINE_AFTER_PINGS` | no | `5` | how many pings a gateway may miss in a row before it is called offline |
| `SCALE_DEVICE_PING_HISTORY_DAYS` | no | `7` | how far back a board's ping history reaches. Each board drops its own rows past this as it pings |

Logs are one JSON object per line, and every line carries the `request_id` of
the request that produced it. A caller may set its own with an `X-Request-ID`
header; the response always carries it back.

A recorded weigh-in logs `field_b`, `sequence`, `rssi` and `locks` alongside the
weight — the parts of the advertisement that describe reception rather than the
weigh-in. `field_b` has no confirmed meaning yet; see §6 of the protocol spec.
`locks` is how many locked advertisements the gateway heard before deciding the
weigh-in had settled: the weight it reports is the last of them, so a count near
one is a gateway that heard the end of the weigh-in only just in time. A ping logs
everything it carried, so the history of a board's health is in the logs even
though only its latest state is in the database.

### The database on the host

`make server-up` bind-mounts `server/data/` into the container, so the file is
an ordinary one on this machine:

```
sqlite3 server/data/weights.db 'SELECT weight_kg, recorded_at FROM readings'
```

A bind mount keeps the host's ownership, so the container runs as the user that
owns that directory — which is what `SCALE_UID` and `SCALE_GID` in
`server/compose.yaml` are for. `make` fills them from `id -u` and `id -g`;
running `docker compose` by hand needs them set. `SCALE_DATA_DIR` moves the
directory somewhere else.

The schema is versioned by `PRAGMA user_version` and migrated at startup. A file
written by a newer build stops the server rather than being upgraded backwards.

## Building in the Arduino IDE

`make` carries the board options in the FQBN, but the IDE does not read them.
BLE and WiFi together need more than the 1.2 MB the board's default partition
gives an application, so set **Tools -> Partition Scheme -> Minimal SPIFFS
(1.9MB APP with OTA/128KB SPIFFS)**. Building against the default fails with
`text section exceeds available space in board`.

On the S3 also set **Tools -> Flash Mode -> DIO 80MHz**. Its flash reads back
`0xff` once the QIO bootloader takes it out of DIO, and a board that flashes and
verifies then boot-loops on `Failed to verify partition table`.

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

## Where the secrets live

The passphrase and the signing key live in the ESP32's NVS partition, written
over serial and never part of a build. NVS is not encrypted, so anyone who can
read the flash — physically, over USB, with `esptool read_flash` — can recover
both. Guarding against that needs flash encryption burned into eFuse, which is a
one-way change to the board.
