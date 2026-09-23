const integer = new Intl.NumberFormat('en-US')

export function formatInt(value: number): string {
  return integer.format(value)
}

/** Micros (millionths of a dollar) as dollars, with fixed decimals so a
 * column of costs lines up. */
export function formatCost(micros: number, decimals = 4): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(micros / 1_000_000)
}

const dateTime = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso))
}

const day = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })

/** A UTC day bucket, labelled as that UTC day. */
export function formatDay(iso: string): string {
  return day.format(new Date(iso))
}

const date = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric' })

export function formatDate(iso: string): string {
  return date.format(new Date(iso))
}
