# The gateway

## Flashing

```
make install        # once, installs the pinned ESP32 core
make upload
make monitor
make firmware-test  # decodes the spec's reference payloads, no board needed
```

`make upload` reads the attached chip and flashes for it, so an ESP32, an
ESP32-S3 and an ESP32-C3 all take the same command. `make compile` has no board
to ask and builds for the plain ESP32; `make compile BOARD=esp32s3` targets
another.

### Building in the Arduino IDE

`make` carries the board options in the FQBN, but the IDE does not read them.
BLE and WiFi together need more than the 1.2 MB the board's default partition
gives an application, so set **Tools -> Partition Scheme -> Minimal SPIFFS
(1.9MB APP with OTA/128KB SPIFFS)**. Building against the default fails with
`text section exceeds available space in board`.

On the S3 also set **Tools -> Flash Mode -> DIO 80MHz**. Its flash reads back
`0xff` once the QIO bootloader takes it out of DIO, and a board that flashes and
verifies then boot-loops on `Failed to verify partition table`.

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
covered in [security](security.md).
