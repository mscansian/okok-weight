// One board, at length. The gateways page holds what a ping last said; this one
// holds the run of them, because a board that fails now and then leaves nothing
// in any single ping — only in how the pings before it were trending, and in how
// long each run before this one managed to last.

const REFRESH_MS = 30_000
const HISTORY_LIMIT = 200

// The plot's own space, in proportions of its box. The svg stretches to
// whatever width the column has and the marker over the line is an element in
// the page, so one set of coordinates serves both. The pad is what the line
// keeps clear above and below its extremes, which is where the bounds sit.
const SPARK_PAD = 12

// A run the board did not report the length of is timed by the last ping before
// it stopped, which can only ever be an underestimate — the board was alive when
// it sent that one and died some unknown way into the next interval.
function completedRuns(pings) {
  const oldestFirst = [...pings].reverse()
  const runs = []
  let earlier = null

  for (const ping of oldestFirst) {
    // Uptime only ever climbs within a run, so a fall is a boot, and the reason
    // that boot reports is how the run before it ended.
    if (earlier !== null && ping.uptime_ms < earlier.uptime_ms) {
      const reported = ping.previous_uptime_ms
      runs.push({
        endedAt: ping.recorded_at,
        lastedMs: reported ?? earlier.uptime_ms,
        atLeast: reported === null || reported === undefined,
        reason: ping.reset_reason,
      })
    }
    earlier = ping
  }

  return runs.reverse()
}

// One line ready to draw: the polyline, the bounds it was fitted to, and the
// samples behind it. The bounds travel with the shape because a line fitted to
// its own range says nothing about where that range sits until they are printed
// beside it, and the samples because the line is worth reading a point at a
// time as well as end to end.
function trendPlot(pings, field) {
  const oldestFirst = [...pings].reverse()
  const known = oldestFirst
    .map((ping) => ping[field])
    .filter((value) => value !== null && value !== undefined)
  if (known.length < 2) return null

  const low = Math.min(...known)
  const high = Math.max(...known)
  // A flat series would divide by zero and deserves a flat line, not a gap.
  const span = high - low || 1
  const step = 100 / (oldestFirst.length - 1)
  const height = 100 - SPARK_PAD * 2

  // A ping that carried nothing for this field keeps its place along the width,
  // so a gap in one reading does not shift the rest of the line in time.
  const points = oldestFirst
    .map((ping, index) => {
      const value = ping[field]
      if (value === null || value === undefined) return null
      return {
        at: ping.recorded_at,
        value,
        left: index * step,
        top: 100 - SPARK_PAD - ((value - low) / span) * height,
      }
    })
    .filter(Boolean)

  return {
    points,
    polyline: points.map((point) => `${point.left.toFixed(2)},${point.top.toFixed(2)}`).join(' '),
    low,
    high,
  }
}

// The sample under the pointer. The plot is drawn in proportions of its box, so
// the pointer only has to be turned into the same proportion to be compared.
function nearestSample(points, event) {
  const box = event.currentTarget.getBoundingClientRect()
  const left = ((event.clientX - box.left) / box.width) * 100
  return points.reduce((nearest, point) =>
    Math.abs(point.left - left) < Math.abs(nearest.left - left) ? point : nearest)
}

// The exact figure beside the rounded one. Empty rather than absent for a board
// too old to report it, because the rounded one alongside already says so.
function rawBytes(value) {
  return value === null || value === undefined ? '' : `${value.toLocaleString()} B`
}

function dbm(value) {
  return value === null || value === undefined ? '—' : `${value} dBm`
}

// The unit belongs to the reading rather than to the page, so each line carries
// the formatter for its own.
const TRENDS = [
  { label: 'free_heap', field: 'free_heap', format: kb },
  { label: 'min_free_heap', field: 'min_free_heap', format: kb },
  { label: 'wifi_rssi', field: 'wifi_rssi', format: dbm },
]

// The SDK's own numbers. Only the ones a board on a wireless network actually
// meets are worth spelling out; the rest are better as the number than as a
// guess at what it meant.
const DISCONNECT_REASONS = {
  0: 'none',
  2: 'auth expired',
  8: 'left the network',
  15: '4-way handshake timeout',
  200: 'beacon timeout',
  201: 'no access point found',
  202: 'auth failed',
  203: 'association failed',
  204: 'handshake timeout',
}

function disconnectReason(code) {
  if (!code) return 'none'
  return `${DISCONNECT_REASONS[code] ?? 'reason'} (${code})`
}

document.addEventListener('alpine:init', () => {
  Alpine.data('gateway', () => ({
    device: null,
    pings: [],
    loading: true,
    problem: null,
    missing: false,
    checkedAt: null,

    // The url is the address. Nothing is filled into this page before it is
    // served, so this is where the page learns which board it is about.
    address: decodeURIComponent(location.pathname.split('/').pop()),

    init() {
      this.load()
      setInterval(() => this.load(), REFRESH_MS)
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') this.load()
      })
    },

    ago,
    stamp,
    full,
    duration,
    kb,
    rawBytes,
    disconnectReason,
    nearestSample,

    get runs() {
      return completedRuns(this.pings)
    },

    // How the runs ended, which is the whole question a board that keeps
    // restarting raises. Counted over what is held rather than over all time,
    // so it says what has been happening lately.
    get brownoutRuns() {
      return this.runs.filter((run) => run.reason === 'brownout').length
    },

    // The typical run, which reads better than the mean when one long stretch
    // sits among a run of short ones.
    get medianRunMs() {
      const lengths = this.runs.map((run) => run.lastedMs).sort((a, b) => a - b)
      if (!lengths.length) return null
      const middle = Math.floor(lengths.length / 2)
      return lengths.length % 2 ? lengths[middle] : Math.round((lengths[middle - 1] + lengths[middle]) / 2)
    },

    // Only the lines there is enough held to draw.
    get trends() {
      return TRENDS
        .map((line) => {
          const plot = trendPlot(this.pings, line.field)
          return plot && { ...line, ...plot }
        })
        .filter(Boolean)
    },

    async load() {
      try {
        const [device, history] = await Promise.all([
          readJson(`/api/v1/devices/${this.address}`),
          readJson(`/api/v1/devices/${this.address}/pings?limit=${HISTORY_LIMIT}`),
        ])
        this.device = device
        this.pings = history.items
        this.checkedAt = new Date().toISOString()
        this.problem = null
      } catch (error) {
        if (error.message.includes('404')) {
          this.missing = true
        } else {
          this.problem = `could not reach the server — ${reason(error)}`
        }
      } finally {
        this.loading = false
      }
    },
  }))
})
