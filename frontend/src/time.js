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
// Every timestamp in the payloads is UTC ISO-8601 as stored by the daemon.

const FORMAT = {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: false, timeZoneName: 'short',
}

// `never`, not an empty string: a timestamp that does not exist is a fact
// worth stating, and the callers pair it with the um-unknown treatment
// (NodeCard's "last seen never" is the P0 behaviour this preserves).
export function localTime (iso, tz, absent = 'never') {
  if (!iso) return absent
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso   // show what we were given
  try {
    return new Intl.DateTimeFormat('en-CA', { ...FORMAT, timeZone: tz || 'UTC' }).format(date)
  } catch {
    // An unknown zone name must not blank a timestamp that is otherwise fine.
    return new Intl.DateTimeFormat('en-CA', { ...FORMAT, timeZone: 'UTC' }).format(date)
  }
}
