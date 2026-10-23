// The gateways page: everything a ping reported, which is what the serial
// console would tell you if the board had a cable in it.

const REFRESH_MS = 30_000

document.addEventListener('alpine:init', () => {
  Alpine.data('gateways', () => ({
    devices: [],
    loading: true,
    saving: false,
    problem: null,
    editing: null,
    draft: '',
    checkedAt: null,

    init() {
      this.load()
      setInterval(() => this.refresh(), REFRESH_MS)
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') this.refresh()
      })
    },

    get reporting() {
      return this.devices.filter((device) => device.online).length
    },

    ago,
    stamp,
    full,
    duration,
    kb,

    async load() {
      try {
        const gateways = await readJson('/api/v1/devices')
        this.devices = gateways.items
        this.checkedAt = new Date().toISOString()
        this.problem = null
      } catch (error) {
        this.problem = `could not reach the server — ${reason(error)}`
      } finally {
        this.loading = false
      }
    },

    // The timer must not pull a board out from under a name being typed into it.
    refresh() {
      if (this.editing === null) this.load()
    },

    edit(device) {
      this.editing = device.address
      this.draft = device.name ?? ''
    },

    cancel() {
      this.editing = null
      this.draft = ''
    },

    // An empty box clears the name, which is what the server takes null to mean.
    async save(device) {
      const name = this.draft.trim()
      this.saving = true
      try {
        const response = await fetch(`/api/v1/devices/${device.address}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name || null }),
        })
        if (response.status === 422) throw new Error('64 characters at most')
        if (response.status === 404) throw new Error('the server has no record of this board')
        if (!response.ok) throw new Error(`the server answered ${response.status}`)

        Object.assign(device, await response.json())
        this.editing = null
        this.draft = ''
        this.problem = null
      } catch (error) {
        // The name stays in the box so it is not lost to a failed save.
        this.problem = `could not rename ${device.address} — ${reason(error)}`
      } finally {
        this.saving = false
      }
    },
  }))
})
