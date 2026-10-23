// Turning what the API sends into what a person reads. Shared by both pages,
// which is why it is loaded as its own script rather than folded into either.

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

const UNITS = [
  ['year', 31_536_000_000],
  ['month', 2_592_000_000],
  ['day', 86_400_000],
  ['hour', 3_600_000],
  ['minute', 60_000],
  ['second', 1000],
]

function ago(iso) {
  if (!iso) return 'never'
  const elapsed = Date.now() - new Date(iso).getTime()
  for (const [unit, size] of UNITS) {
    if (Math.abs(elapsed) >= size || unit === 'second') {
      return relative.format(-Math.round(elapsed / size), unit)
    }
  }
}

function stamp(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

// Every part of it, for the places that exist to be precise.
function full(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function day(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function duration(ms) {
  if (ms === null || ms === undefined) return '—'
  const days = Math.floor(ms / 86_400_000)
  const hours = Math.floor((ms % 86_400_000) / 3_600_000)
  const minutes = Math.floor((ms % 3_600_000) / 60_000)
  if (days) return `${days}d ${hours}h`
  if (hours) return `${hours}h ${minutes}m`
  if (minutes) return `${minutes}m`
  return `${Math.round(ms / 1000)}s`
}

function kb(bytes) {
  if (bytes === null || bytes === undefined) return '—'
  return `${(bytes / 1024).toFixed(1)} KB`
}

// fetch rejects with a TypeError when the request never got anywhere, which is
// a different thing to report than a server that answered and refused.
function reason(error) {
  return error instanceof TypeError ? 'the server did not answer' : error.message
}

async function readJson(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`the server answered ${response.status}`)
  return response.json()
}
