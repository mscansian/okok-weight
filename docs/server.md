# The server

The server is a FastAPI service. It stores the gateway's weigh-ins in SQLite,
along with the latest report from each gateway. For a weigh-in it stores only
the weight and the arrival time. The scale has no clock, and the rest of the
advertisement is about reception, not the weigh-in, so that part goes to the
logs.

Run it on the machine that `url` points to:

```
make server-up     # container, database in server/data/ on this host
make server-down
```

Or run it from the checkout, without Docker:

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
| `POST /api/v1/readings` | stores one weigh-in |
| `GET /api/v1/readings` | the history, newest first, `?address=&limit=&offset=` |
| `POST /api/v1/devices/{address}/ping` | stores a gateway's report that it is alive |
| `GET /api/v1/devices` | every gateway that has ever pinged |
| `GET /api/v1/devices/{address}` | one gateway |
| `PUT /api/v1/devices/{address}` | sets the board's name |
| `GET /api/v1/health` | responds only after SQLite does |

Every route is under `/api/v1`. The gateway has that prefix built in instead of
reading it from the stored url, so `url` is just the server's host. To move a
board to a later API version, you reflash it.

Interactive docs are at `/docs`.

## The pages

`/` is a weight monitor. It shows the last weigh-in in large type, the way the
scale shows it, the change since the one before, a chart of readings over time,
and the history below that. Hover over a date in the history to see exactly
when the weigh-in happened and, if the gateway had to hold it, how long it
waited.

`/devices` shows the gateways, and is deliberately more raw. It has one row per
board, with every field named as the API names it and a readable value next to
the raw one. It is the same information `make show` gives over a cable, for
boards that run without one. You can only edit the name, because it is the only
column a ping doesn't overwrite. Click it and type.

The weight page also fetches the gateways, but only to show whether any are
still reporting. If a board goes quiet, the newest weigh-in on the page may be
older than the newest one actually taken. That is the one hardware fact worth
showing next to the weight.

Neither page keeps state or its own copy of the data. Both read the JSON routes
above, so they show nothing that `curl` can't also get. They refresh every 30
seconds and when you come back to the tab.

The read routes have no authentication. See [security](security.md) before
putting the server on a public hostname.

## Several gateways, one endpoint

Every reading carries the `address` of the scale it came from, so any number of
gateways can post to one server. Each scale's history is
`GET /api/v1/readings?address=…`. One catch: if two gateways are in range of the
same scale, each one names the same weigh-in with its own id, so it is stored
twice. The server can't tell those apart. `GET /api/v1/devices` will show both
boards listing that scale in `scale_addresses`, which helps you spot the problem
but doesn't fix it.

## Delivering a weigh-in twice

The gateway gives each weigh-in an `event_id` when the scale first locks. It is
a UUID drawn from the ESP32's hardware entropy source, and the gateway reuses it
whenever it retries the delivery. The server answers a repeat with `200` and the
reading from the first delivery, instead of `201` and a second weigh-in. That
makes retries safe: if a POST arrived but its response was lost, the retry
returns the reading that already exists.

`measured_at` is when the weigh-in happened and `recorded_at` is when it reached
the server. They match when the gateway delivers right away, and differ by
however long the gateway held the weigh-in. Readings are sorted and paginated by
`measured_at`, so a late delivery appears in its proper place in the history,
not at the top.

## Is the gateway alive?

Every five minutes the gateway posts to `/api/v1/devices/{address}/ping`, using
its own radio's address. That address is the one name the board has that nobody
had to give it. A board's first ping creates its row in the database. Nothing
needs registering first, because the signing key is what proves a ping is
genuine.

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

The server works out `online` when it builds the response and never stores it,
so a server that was down doesn't leave stale rows claiming a board is up. A
gateway counts as online until it goes quiet for longer than the number of pings
it may miss. The default is five, which at the firmware's five-minute interval
means half an hour of silence before a board is marked offline.

The interval comes from the gateway, not from a server setting, so a board with
a new interval is judged by it from its next ping. To change it, change
`PING_INTERVAL_MS` in the firmware. Nothing else needs to change.

`name` is yours to set, and it is the only column a ping doesn't overwrite. A
board knows its own address but not where you put it:

```
curl -X PUT localhost:8080/api/v1/devices/a0:b7:65:12:34:56 \
  -H 'Content-Type: application/json' -d '{"name": "bathroom"}'
```

Send `null` to clear the name. The board must have pinged at least once, because
its first ping creates its row.

### What a ping carries, and why

Liveness is the smallest part of a ping. The rest is what the serial console
would show you if a cable were plugged in, and a board doing its job usually has
no cable.

| Field | What it is worth |
|---|---|
| `scale_addresses` | which scales the board was told to listen for. In the reading history, a gateway watching the wrong scale looks the same as a gateway nobody has stood on, and this field tells them apart. An empty list means the board was told no scales. A missing field means firmware too old to take more than one |
| `queued` | weigh-ins taken but not yet delivered. A non-zero value means the history is behind what the scale has seen |
| `uptime_ms` | a board that answers every ping but whose uptime keeps going back to zero is rebooting. Liveness alone would never show that |
| `reset_reason` | why the board last started. A brownout means fixing the power supply. A panic means fixing the firmware |
| `boots`, `brownouts` | how many times this board has started, and how many of those starts the brownout detector caused. `reset_reason` only covers the last boot, so for a supply that sags now and then it shows that a brownout happened but not how often. The board counts both in its own storage, and they survive reboots and reflashes, so you can read the rate from `GET /api/v1/devices` instead of the logs |
| `free_heap`, `largest_free_block` | how much memory the board had free while building the ping. A TLS session needs two 16 KB record buffers, each in one piece, so a fragmented heap cuts the board off from the server while the total still looks fine. The second number runs out first |
| `ssid`, `wifi_rssi` | which network the board joined, and its signal strength |
| `firmware_version` | which build is on which board, for when two boards behave differently |

`scale_addresses` is what the board was told, not what it found. It is the
setting as provisioned, so an entry may be only the five octets the
advertisement carries instead of a full address. `client_ip` works the other
way. The gateway can't know the public address its traffic leaves from, so the
server records the address the ping arrived from.

Behind a reverse proxy, every request arrives from the proxy's address. The peer
address then names the proxy, and everyone behind it shares one rate-limit
allowance, so a single busy caller could use up the gateway's share. To avoid
that, the server looks for the caller in `CF-Connecting-IP`, then `X-Real-IP`,
then the first entry of `X-Forwarded-For`. It uses the peer address only when
none of them is set.

The order matters. nginx sets `X-Real-IP` to the address that called it, which
behind Cloudflare is a Cloudflare edge. If the server read it before
Cloudflare's own header, it would record the edge as the visitor.

Anyone sending a request can fake these headers, and that's accepted. The
address only feeds a rate limit and a log line. Nothing gets in because of it:
weigh-ins and pings get in by their signature, whatever address they come from.
A header value that isn't an address is logged and skipped.

## Configuration

| Variable | Required | Default | What it is |
|---|---|---|---|
| `SCALE_DB_PATH` | yes | — | where the SQLite file lives. It has no default on purpose, so the history never ends up somewhere you didn't choose |
| `SCALE_GATEWAY_KEY` | yes | — | the key deliveries are signed with, the same one the gateway was provisioned with. At least 32 characters |
| `SCALE_HOST` | no | `0.0.0.0` | interface to bind |
| `SCALE_PORT` | no | `8080` | port to bind |
| `SCALE_LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `SCALE_MAX_BODY_BYTES` | no | `4096` | bodies larger than this are rejected without being read |
| `SCALE_RATE_LIMIT_PER_MINUTE` | no | `120` | the limit per client |
| `SCALE_DEFAULT_PAGE_SIZE` | no | `50` | page size when `limit` is omitted |
| `SCALE_MAX_PAGE_SIZE` | no | `500` | largest `limit` accepted |
| `SCALE_DEVICE_OFFLINE_AFTER_PINGS` | no | `5` | how many pings in a row a gateway can miss before it counts as offline |
| `SCALE_DEVICE_PING_HISTORY_DAYS` | no | `7` | how far back each board's ping history goes. Each time a board pings, the server deletes that board's rows older than this |

Logs are one JSON object per line, and every line carries the `request_id` of
the request that produced it. A caller can set its own with an `X-Request-ID`
header, and the response always sends it back.

A recorded weigh-in logs `field_b`, `sequence`, `rssi` and `locks` along with
the weight. These are the parts of the advertisement about reception, not the
weigh-in. `field_b` has no confirmed meaning yet; see §6 of the protocol spec.
`locks` is how many locked advertisements the gateway heard before it decided
the weigh-in had settled. The reported weight is the last of them, so a count
near one means the gateway only just caught the end of the weigh-in. A ping logs
everything it carried, so the logs keep a board's health history even though the
database only keeps its latest state.

## The database on the host

`make server-up` bind-mounts `server/data/` into the container, so the database
is an ordinary file on the host:

```
sqlite3 server/data/weights.db 'SELECT weight_kg, recorded_at FROM readings'
```

A bind mount keeps the host's file ownership, so the container has to run as the
user that owns that directory. That is what `SCALE_UID` and `SCALE_GID` in
`server/compose.yaml` are for. `make` sets them from `id -u` and `id -g`. If you
run `docker compose` yourself, set them by hand. `SCALE_DATA_DIR` moves the
directory somewhere else.

The schema version is kept in `PRAGMA user_version`, and the server migrates the
schema at startup. If the file was written by a newer build, the server stops
instead of downgrading it.
