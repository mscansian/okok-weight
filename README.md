# okok-weight

A small build that gets your weight off a cheap Bluetooth bathroom scale and
onto a server you own, with no phone app involved. It's an ESP32 in a
3D-printed case that sits near the scale, the firmware it runs, and the server
it reports to.

Scales like this one usually only talk to their vendor's app (here, OKOK). It
turns out the scale never needs the app at all: every reading goes out in plain
BLE advertisements to anyone nearby. So an ESP32 sits in range, listens for
those advertisements, works out the weight, and posts it to a small server. The
server keeps the history in SQLite and shows it on a web page.

```
 bathroom scale  ──BLE broadcast──▶  ESP32 gateway  ──signed HTTPS POST──▶  server
 (never paired)                      (only listens)                        (SQLite + web page)
```

You step on the scale and a few seconds later the reading is on the page. The
gateway never connects to the scale and never transmits over BLE. If the network
is down, it holds weigh-ins and sends them later.

## What's in here

| Part | Where | What it is |
|---|---|---|
| Case | [Onshape](https://cad.onshape.com/documents/4e177e05d399b3c231c1a8f9/w/4f14c960c2046563de17c3bf/e/939c1c707eed9fb2c944c356) | the 3D-printable cover for the ESP32 |
| Firmware | `firmware/scale-gateway/` | the ESP32 sketch (Arduino), for the ESP32, ESP32-S3 and ESP32-C3 |
| Server | `server/` | the FastAPI server, the weight page and the gateway status page |
| Tools | `tools/` | scripts that talk to the board over serial: provisioning and status |
| Protocol | `specs/scale-ble-protocol.md` | the scale's advertisement format, reverse-engineered |

## What you need

- A bathroom scale that broadcasts its readings over BLE, like the one
  described in the protocol doc.
- An ESP32, ESP32-S3 or ESP32-C3 dev board, and a USB power supply for it.
- The case, printed from the Onshape model.
- A machine with Docker to run the server. It can be anywhere the gateway can
  reach, on your LAN or in the cloud.

## Quickstart

You need [`arduino-cli`](https://arduino.github.io/arduino-cli/),
[`uv`](https://docs.astral.sh/uv/) and Docker. Generate a key that the gateway
and the server share:

```
openssl rand -hex 32
```

Start the server on a machine the gateway can reach. It asks for the key:

```
read -rs SCALE_GATEWAY_KEY && export SCALE_GATEWAY_KEY
make server-up
```

Flash the board and give it its settings. The provisioning script prompts for
the WiFi password and the key, so neither ends up in your shell history:

```
make install        # once, installs the pinned ESP32 core
make upload         # detects which ESP32 is plugged in
export WIFI_SSID='your-network'
export SERVER_URL='https://scale.example.com'
export SCALE_ADDRESSES='08:B8:D0:12:34:56'   # your scale's BLE address
make provision
```

Step on the scale, then open the server in a browser or ask the API:

```
curl localhost:8080/api/v1/readings
```

Tests run without a board: `make firmware-test` and `make server-test`.

## Docs

- [Gateway](docs/gateway.md): flashing, provisioning, power and brownouts,
  what happens when the network is down.
- [Server](docs/server.md): the API, the web pages, gateway health,
  configuration.
- [Security](docs/security.md): how deliveries are signed, and what is not
  protected. **The read routes have no authentication.** Don't put the server
  on a public hostname unless something in front of it handles auth.
