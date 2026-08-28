// One place that turns a stored UTC instant into something an operator reads.
//
// The zone is the BOX's, not the viewer's: toLocaleString() with no timeZone
// renders wherever the browser happens to be, which matches the server only by
// coincidence - a laptop on a plane would quietly relabel every timestamp in
// the pane. The endpoints report the system zone (common.php's
// um_local_timezone, read from /etc/localtime because Unraid runs PHP with
// date.timezone unset and date_default_timezone_get() answers UTC on a box
// whose own clock says EDT).
//
// The clock follows Unraid's own Settings -> Date & Time, reported as
// `clock12` (dynamix.cfg's [display] time="%I:%M %p" on Raven). The DATE half
// deliberately does not: dynamix stores strftime formats like "%c", which
// translate into nothing Intl speaks, and YYYY-MM-DD is unambiguous in every
// locale and sorts.
//
// Two formatters rather than one: en-CA gives the ISO date but renders a
// 12-hour clock as "05:45:18 p.m." - leading zero, periods - while en-US gives
// "5:45:18 PM" but an American date. Each does the half it does well.
//
// Every timestamp in the payloads is UTC ISO-8601 as stored by the daemon.

const DATE = { year: 'numeric', month: '2-digit', day: '2-digit' }
const TIME = { hour: 'numeric', minute: '2-digit', second: '2-digit', timeZoneName: 'short' }

function parts (date, tz, clock12) {
  const day = new Intl.DateTimeFormat('en-CA', { ...DATE, timeZone: tz }).format(date)
  const time = new Intl.DateTimeFormat('en-US',
    { ...TIME, hour12: !!clock12, timeZone: tz }).format(date)
  return `${day}, ${time}`
}

// `never`, not an empty string: a timestamp that does not exist is a fact
// worth stating, and the callers pair it with the um-unknown treatment
// (NodeCard's "last seen never" is the P0 behaviour this preserves). A value
// we cannot parse is shown as it came - "never" would be a claim about the
// node when the truth is a claim about us.
export function localTime (iso, tz, clock12 = false, absent = 'never') {
  if (!iso) return absent
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  try {
    return parts(date, tz || 'UTC', clock12)
  } catch {
    // An unknown zone name must not blank a timestamp that is otherwise fine.
    return parts(date, 'UTC', clock12)
  }
}
