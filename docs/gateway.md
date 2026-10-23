# The gateway

## Flashing

```
make install        # once, installs the pinned ESP32 core
make upload
make monitor
make firmware-test  # decodes the spec's reference payloads, no board needed
```

`make upload` reads the chip on the other end of the cable and builds for it, so
the same command works for an ESP32, an ESP32-S3 and an ESP32-C3. `make compile`
has no board to read, so it builds for the plain ESP32. Use
`make compile BOARD=esp32s3` to build for another chip.

### Building in the Arduino IDE

`make` passes the board options in the FQBN, but the IDE ignores them. BLE and
WiFi together need more than the 1.2 MB the default partition gives an
application, so set **Tools -> Partition Scheme -> Minimal SPIFFS (1.9MB APP
with OTA/128KB SPIFFS)**. With the default partition, the build fails with
`text section exceeds available space in board`.

On the S3, also set **Tools -> Flash Mode -> DIO 80MHz**. Its flash reads back
`0xff` once the QIO bootloader switches it out of DIO. The board flashes and
verifies, then boot-loops on `Failed to verify partition table`.

## Provisioning

The firmware has no network settings built in, so the same image runs on any
board. You send the settings over serial and the board stores them in NVS. They
survive reboots and reflashes, so you provision a board once, not after every
`make upload`.

`make provision` reads these from the environment:

| Variable | Required | Example | What it is |
|---|---|---|---|
| `WIFI_SSID` | yes | `your-network` | the network the gateway joins |
| `WIFI_PASSWORD` | yes, but **leave unset** | — | the network's passphrase (see below) |
| `SERVER_URL` | yes | `https://scale.example.com` | where the server is, with no route on the end. The gateway posts weigh-ins and pings to different routes. The gateway must be able to reach it, so never use `localhost`. With `https://` the body is encrypted but the certificate is not verified. `http://` is fine for a server on the LAN |
| `SCALE_GATEWAY_KEY` | yes, but **leave unset** | — | the key the gateway signs deliveries with (see below) |
| `SCALE_ADDRESSES` | yes | `AA:BB:CC:DD:EE:FF` | the scales to listen for, up to four, separated by spaces. Use the full MAC or only its last five octets, with colons or dashes, in any case |

```
export WIFI_SSID='your-network'
export SERVER_URL='https://scale.example.com'
export SCALE_ADDRESSES='AA:BB:CC:DD:EE:FF 08:B8:D0:12:34:56'
make provision
```

One gateway can serve every scale within radio range. Each weigh-in carries the
address of the scale it was taken on, so two scales on one board keep separate
histories. The gateway ignores any scale that is not on the list. That includes
the same model next door, which broadcasts valid readings that don't belong in
your history.

`make provision` prompts for anything left unset. **Leave `WIFI_PASSWORD` and
`SCALE_GATEWAY_KEY` unset on purpose.** Their prompts don't echo, so neither
secret ends up in your shell history, in a file or on a command line. Exporting
them works too, but then they sit in your history and in whatever file exported
them.

The gateway and the server both need `SCALE_GATEWAY_KEY`, under the same name.
The server uses it to check signatures. Generate one with
`openssl rand -hex 32` and keep it somewhere you can paste it from twice.

`make show` prints the stored settings, without the two secrets. `make ping`
makes the board report to the server right away and prints the round trip. The
board also pings on its own every five minutes, and right away when the link
comes up or the url changes.

You can also type the settings by hand in `make monitor`, using `network`,
`passphrase`, `url`, `key`, `scale`, `ping`, `show`, `connect` and `forget`. The
board takes everything after the command word as is, so values with spaces need
no quotes.

## Keeping the board cool enough to stay up

The gateway keeps no state it cannot rebuild, so a reset only loses the
weigh-ins still in the queue. But a board that resets because its supply sagged
will usually do it again a few hours later. On the firmware side, what matters
is the current the board draws while idle, because that heats the regulator all
day:

| Setting | What it trades |
|---|---|
| `WIFI_POWER_SAVE` | how much of the time between the access point's beacons the WiFi radio spends parked. Waking for every beacon draws the most current of the parking modes, and makes the smallest jump in current when the radio wakes. Sleeping through beacons does the opposite on both counts. It also delays the inbound half of a POST by up to the listen interval, but `age_ms` keeps that delay out of the history |
| `CPU_FREQUENCY_MHZ` | the clock speed, fixed for the board's whole run. Nothing between weigh-ins is compute-bound, so a lower clock only slows the TLS handshake, and every ping reports that round trip. 80 MHz is the floor. Below it the radios stop working and the peripheral bus drops below 80 MHz, so the console's baud rate and the timers lose their divisors |
| `IDLE_SCAN_INTERVAL_MS` | how often the BLE receiver listens while nobody is on the scale, which is nearly all the time. A longer interval means joining a weigh-in a little after it starts. It can't produce a different weight: once the receiver hears a lock it listens continuously, so it never misses the end of a weigh-in, which is where the reported weight comes from |

A `static_assert` ties both scan constants to how the scale behaves. If the
receiver would listen too rarely to catch a broadcast, or could call a weigh-in
settled between two listens, the build fails. Without that check, the gateway
would quietly report weights that are too light.

The firmware is only half of the fix. A brownout means the 3V3 rail dropped
below about 2.43 V. That is far below what the ESP32 needs, so the regulator is
collapsing, not just sagging. The usual cause is a dev board's own LDO on a long
USB cable with no bulk capacitance. Feeding 3V3 directly from a supply with
headroom helps more than any of the settings above.

To see whether any of this worked, compare `brownouts` with `boots` in
`GET /api/v1/devices`. The `locks` count on each recorded weigh-in tells you
whether the receiver still hears weigh-ins in full.

## When the link goes down

If the network is down when someone weighs in, the gateway keeps the weigh-in.
It queues it, retries every 30 s, and gives up after 24 hours. That covers a
router reboot or a night of ISP trouble, and keeps day-old readings out of the
history. Up to 64 weigh-ins can wait at once. When the queue is full, the oldest
one is dropped. `make show` reports how many are waiting.

Each delivery says how long ago the weigh-in happened, not when. The server
subtracts that age from the arrival time. The ESP32's clock has no battery, so
after a power cut the board doesn't know the date, and with the network down it
can't ask. It always knows how long ago something happened, counting from boot.

The queue lives in RAM, so a reset loses any weigh-ins still waiting. That only
happens if the board resets during an outage, which is rare. In exchange, the
board doesn't write to flash on every weigh-in, and it never has to timestamp
readings taken before it knew the time.

## When the link is up and the server is still unreachable

A TLS session needs two 16 KB record buffers before it sends any data, and BLE
and WiFi hold their own memory the whole time. So https is where the gateway
runs out of heap first. The error doesn't say so: when an allocation fails
inside mbedTLS, it shows up as a refused connection:

```
tls: X509 - A fatal error occurred, eg the chain is too long or the vrfy callback failed
heap: 60020 free, largest block 34804, going in
ping: … failed, status -1 (connection refused)
```

Watch the second number, the largest free block. Memory split into pieces too
small to use still counts as free everywhere else, and a POST needs its memory
in one piece. Every ping also reports `free_heap` and `largest_free_block`, so
you can follow the trend in `GET /api/v1/devices` without a cable. A ping only
arrives if it found enough memory, though, so for a board that has stopped,
watch the console instead.

Two things free up memory. The firmware hands the Bluetooth Classic half of the
controller back to the heap before BLE starts, because the gateway only scans
and never uses it. And the gateway does not verify the certificate chain, which
saves more. [Security](security.md) covers that trade.
