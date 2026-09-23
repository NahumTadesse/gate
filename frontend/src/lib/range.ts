const DAY_MS = 86_400_000

/** The last `days` UTC days, today included: [from, to) as ISO strings, and
 * each day's start, so days with no usage can be shown as zero. */
export function lastDays(days: number, now = new Date()) {
  const tomorrow = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)
  const from = tomorrow - days * DAY_MS
  return {
    from: new Date(from).toISOString(),
    to: new Date(tomorrow).toISOString(),
    days: Array.from({ length: days }, (_, i) => new Date(from + i * DAY_MS).toISOString()),
  }
}
