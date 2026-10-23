# The server

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

## The pages

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

The read routes have no authentication. See [security](security.md) before
putting the server on a public hostname.

## Several gateways, one endpoint

Every reading carries the `address` of the scale it came from, so any number of
gateways can post here and each scale's history is `GET /api/v1/readings?address=…`.
One thing that does not follow: two gateways in range of the same scale each
name the same weigh-in for themselves, so it lands twice. Nothing here can tell
those apart, though `GET /api/v1/devices` will show both boards listing that
scale in `scale_addresses`, which is the shape of the problem if not a fix for it.

## Delivering a weigh-in twice

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

## Is the gateway alive?

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

### What a ping carries, and why

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

## Configuration

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

## The database on the host

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
