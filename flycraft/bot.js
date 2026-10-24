// Creates the Mineflayer bot with its connection listeners attached synchronously.
// Listeners registered from Python go over JSPyBridge IPC, so early events (e.g. a
// refused connection) can fire before they exist; Python callbacks also keep the
// bridge alive at exit. Instead, events are recorded in bot.flycraft and Python polls it.

function describe (err) {
  if (!err) return String(err)
  if (err.errors && err.errors.length) return err.errors.map(describe).join('; ')
  return err.message || err.code || String(err)
}

exports.create = function (mineflayer, options) {
  const bot = mineflayer.createBot({ ...options, hideErrors: true })
  const status = { spawned: false, ended: false, kind: null, detail: null, warnings: [] }
  bot.flycraft = status

  const end = (kind, detail) => {
    if (status.ended) return
    status.kind = kind
    status.detail = detail
    status.ended = true
  }

  bot.on('spawn', () => { status.spawned = true })
  bot.on('kicked', (reason) => end('kicked', typeof reason === 'string' ? reason : JSON.stringify(reason)))
  bot.on('error', (err) => {
    // After spawn Mineflayer also emits non-fatal errors (bad sign text, unknown scoreboard...).
    if (status.spawned) status.warnings.push(describe(err))
    else end('error', describe(err))
  })
  bot.on('end', (reason) => end('disconnected', String(reason)))

  // Drained by Python; returns and clears the non-fatal warnings.
  status.takeWarnings = () => status.warnings.splice(0).join('\n')
  return bot
}
