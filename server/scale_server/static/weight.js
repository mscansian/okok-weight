// The weight page: the last weigh-in, what it did, a line through the history.
//
// It also asks for the gateways, but only to answer whether anything is still
// reporting — a board gone quiet means the history is older than it looks, which
// is the one thing about the hardware that belongs on this page. Everything else
// about a board is on its own.

const PAGE = 50

// The server refuses a limit above its own ceiling, and a refresh asks for
// everything already on screen at once.
const MAX_PAGE = 500

const REFRESH_MS = 30_000

// The plot's own coordinate space. The svg scales to its box, so these are
// arbitrary units chosen to make the padding arithmetic readable.
const PLOT = { width: 720, height: 220, padY: 26, padX: 14, dot: 2.6 }

// Below this, the two timestamps on a reading are the same moment as far as
// anyone cares, and the gap is the insert rather than an outage.
const HELD_THRESHOLD_MS = 2000

// Every reading as one path of circles rather than a circle element each: an
// `<svg>` cannot hold a real `<template>`, because the parser builds one in the
// svg namespace where it is an unknown element with no content to clone, so
// there is nothing there for x-for to repeat over.
function dotsPath(points, r) {
  return points
    .map(({ x, y }) => `M ${x - r} ${y} a ${r} ${r} 0 1 0 ${r * 2} 0 a ${r} ${r} 0 1 0 ${-r * 2} 0`)
    .join(' ')
}

// Catmull-Rom through every point, written as the cubic curves svg wants. Each
// control point is held inside the span of the two readings it sits between, so
// a curve drawn through irregular weigh-ins cannot bulge past a weight nobody
// ever stood at.
function spline(points) {
  if (points.length < 2) return ''

  const clamp = (value, a, b) => Math.min(Math.max(value, Math.min(a, b)), Math.max(a, b))
  let path = `M ${points[0].x} ${points[0].y}`

  for (let i = 0; i < points.length - 1; i++) {
    const before = points[i - 1] ?? points[i]
    const from = points[i]
    const to = points[i + 1]
    const after = points[i + 2] ?? to

    const c1 = { x: from.x + (to.x - before.x) / 6, y: from.y + (to.y - before.y) / 6 }
    const c2 = { x: to.x - (after.x - from.x) / 6, y: to.y - (after.y - from.y) / 6 }

    path += ` C ${c1.x} ${clamp(c1.y, from.y, to.y)},`
    path += ` ${c2.x} ${clamp(c2.y, from.y, to.y)},`
    path += ` ${to.x} ${to.y}`
  }

  return path
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / (values.length || 1)
}

// One average per calendar week the readings fall in, dated at the middle of
// the weigh-ins it holds so a week weighed only on Monday does not sit on
// Sunday. Weeks start Monday in the reader's own timezone, which is the week
// the person on the scale lives in. Times arrive oldest first, so a week is
// closed as soon as the next one opens.
function weeklyMeans(times, weights) {
  const weeks = []

  times.forEach((time, index) => {
    const monday = new Date(time)
    monday.setHours(0, 0, 0, 0)
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7))

    const open = weeks[weeks.length - 1]
    if (open && open.start === monday.getTime()) {
      open.times.push(time)
      open.weights.push(weights[index])
    } else {
      weeks.push({ start: monday.getTime(), times: [time], weights: [weights[index]] })
    }
  })

  return weeks.map((week) => ({ time: average(week.times), weight: average(week.weights) }))
}

// The weekly series as the two paths it is drawn with: its top edge, and the
// same edge closed down to the floor of the plot.
//
// A week sits at the middle of the weigh-ins it holds, so the first and last
// weeks stand short of the edges. Their level is carried flat out to the edge
// rather than left as a wall, since a part week's average is what is known
// about it right up to where it ends.
function band(points) {
  if (!points.length) return { edge: '', fill: '' }

  const left = PLOT.padX
  const right = PLOT.width - PLOT.padX
  const last = points[points.length - 1]
  const edge = spline([{ x: left, y: points[0].y }, ...points, { x: right, y: last.y }])

  return { edge, fill: `${edge} L ${right} ${PLOT.height} L ${left} ${PLOT.height} Z` }
}

// The weigh-in under the pointer, by horizontal distance alone: the finger is
// somewhere in a tall plot, and what it is pointing at is a moment in time.
function nearestPoint(points, event) {
  const box = event.currentTarget.getBoundingClientRect()
  const left = ((event.clientX - box.left) / box.width) * 100
  return points.reduce((nearest, point) =>
    Math.abs(point.left - left) < Math.abs(nearest.left - left) ? point : nearest, points[0]) ?? null
}

document.addEventListener('alpine:init', () => {
  Alpine.data('weight', () => ({
    readings: [],
    total: 0,
    boards: 0,
    reporting: 0,
    loading: true,
    fetching: false,
    problem: null,
    checkedAt: null,
    span: 0,
    spans: [
      { label: '30d', days: 30 },
      { label: '90d', days: 90 },
      { label: 'all', days: 0 },
    ],

    init() {
      this.load()
      setInterval(() => this.load(), REFRESH_MS)
      // Coming back to a tab that has been open all day should not leave
      // yesterday's weight on screen until the next tick comes round.
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') this.load()
      })
    },

    // Readings arrive newest first, which is the order the history is read in.
    get latest() {
      return this.readings[0] ?? null
    },

    get change() {
      return this.step(0)
    },

    // A board that has stopped reporting is why the newest weigh-in here may be
    // older than the newest weigh-in taken.
    get silent() {
      return this.boards - this.reporting
    },

    // Oldest first and inside the chosen span, which is the order a line is
    // drawn in.
    get plotted() {
      const since = this.span ? Date.now() - this.span * 86_400_000 : null
      return this.readings
        .filter((reading) => since === null || new Date(reading.measured_at).getTime() >= since)
        .slice()
        .reverse()
    },

    get chart() {
      const points = this.plotted
      const weights = points.map((reading) => reading.weight_kg)
      const low = Math.min(...weights)
      const high = Math.max(...weights)
      const mean = average(weights)

      // A run of identical weights would divide by zero, so it is drawn down
      // the middle instead of against a range it does not have.
      const spread = high - low || 1
      const usableY = PLOT.height - PLOT.padY * 2
      const y = (weight) => PLOT.padY + (1 - (weight - low) / spread) * usableY

      const times = points.map((reading) => new Date(reading.measured_at).getTime())
      const first = times[0]
      const elapsed = times[times.length - 1] - first
      const usableX = PLOT.width - PLOT.padX * 2

      // Weigh-ins are irregular, so the axis is time rather than a count of
      // them — a fortnight's gap should look like one.
      const x = (time) => PLOT.padX + ((time - first) / elapsed) * usableX

      const placed = points.map((reading, index) => ({
        // With every reading at the same instant there is no span to divide by,
        // so they are spread evenly instead.
        x: elapsed ? x(times[index])
          : PLOT.padX + (index / Math.max(points.length - 1, 1)) * usableX,
        y: y(reading.weight_kg),
      }))

      // Every weigh-in is its own point, so the line jumps with a heavy dinner.
      // The week behind it is the same weights with that noise averaged out.
      const weekly = band(weeklyMeans(times, weights).map((week) => ({
        x: elapsed ? x(week.time) : PLOT.width / 2,
        y: y(week.weight),
      })))

      return {
        count: placed.length,
        dots: dotsPath(placed, PLOT.dot),
        line: spline(placed),
        // Every weigh-in again as shares of the plot's own box, which is what
        // the pointer works in: the svg scales uniformly to its width, so a
        // share of the coordinate space is the same share of the rendered box.
        points: placed.map((point, index) => ({
          left: (point.x / PLOT.width) * 100,
          top: (point.y / PLOT.height) * 100,
          weight: weights[index],
          at: points[index].measured_at,
        })),
        weekEdge: weekly.edge,
        weekFill: weekly.fill,
        low: Number.isFinite(low) ? low : 0,
        high: Number.isFinite(high) ? high : 0,
        mean: Number.isFinite(mean) ? mean : 0,
        meanY: Number.isFinite(mean) ? y(mean) : PLOT.height / 2,
        // The svg scales uniformly, so a share of its coordinate height is the
        // same share of its rendered height — which is what puts the label on
        // the line rather than near it.
        meanPct: Number.isFinite(mean) ? (y(mean) / PLOT.height) * 100 : 50,
        swing: points.length > 1 ? weights[weights.length - 1] - weights[0] : null,
        from: points.length ? day(points[0].measured_at) : '',
        to: points.length ? day(points[points.length - 1].measured_at) : '',
      }
    },

    // What this weigh-in did against the one before it. The oldest reading
    // loaded has nothing behind it to compare against — the next page would,
    // which is why this is null rather than zero.
    step(index) {
      const older = this.readings[index + 1]
      if (!older) return null
      return this.readings[index].weight_kg - older.weight_kg
    },

    // How long the gateway sat on this weigh-in before the server got it, which
    // is the only trace an outage at the gateway leaves in the history.
    lag(reading) {
      const waited = new Date(reading.recorded_at) - new Date(reading.measured_at)
      return waited > HELD_THRESHOLD_MS ? duration(waited) : ''
    },

    ago,
    stamp,
    full,
    nearestPoint,

    async load() {
      try {
        const want = Math.min(Math.max(this.readings.length, PAGE), MAX_PAGE)
        const [history, gateways] = await Promise.all([
          readJson(`/api/v1/readings?limit=${want}&offset=0`),
          readJson('/api/v1/devices'),
        ])
        this.readings = history.items
        this.total = history.total
        this.boards = gateways.total
        this.reporting = gateways.items.filter((device) => device.online).length
        this.checkedAt = new Date().toISOString()
        this.problem = null
      } catch (error) {
        this.problem = `could not reach the server — ${reason(error)}`
      } finally {
        this.loading = false
      }
    },

    async more() {
      this.fetching = true
      try {
        const older = await readJson(`/api/v1/readings?limit=${PAGE}&offset=${this.readings.length}`)
        this.readings = this.readings.concat(older.items)
        this.total = older.total
        this.problem = null
      } catch (error) {
        this.problem = `could not load older weigh-ins — ${reason(error)}`
      } finally {
        this.fetching = false
      }
    },
  }))
})
