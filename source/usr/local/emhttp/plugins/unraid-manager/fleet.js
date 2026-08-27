/* Fleet table. Live updates arrive as a nudge over nchan; the data itself
   always comes from the authenticated API. A 30-second timer covers a silent
   stream, and after three minutes with nothing successful the page says so
   rather than showing stale numbers as though they were current. */
(function () {
  var root = document.getElementById('um-fleet');
  if (!root) return;

  var NODES = '/plugins/unraid-manager/api/nodes.php';
  var FALLBACK_MS = 30000;
  var STALE_MS = 180000;
  /* Seeded at load, not left at 0: "never succeeded once" is the primary case
     the banner exists for — a managerd that is down when the tab opens. With a
     0 seed the `lastGood &&` guard below would suppress the banner forever in
     exactly that case. */
  var lastGood = Date.now();

  var STATE_LABEL = {ok: '✓ OK', degraded: '⚠ Degraded', unknown: '? Unknown'};

  function bytes(n) {
    if (n === null || n === undefined) return '—';
    var units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'], i = 0, v = Number(n);
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(v >= 10 || i === 0 ? 0 : 1) + ' ' + units[i];
  }

  function capacityCell(cell, node) {
    var cap = node.capacity;
    if (!cap || !cap.total) {
      /* Constraint 3: an array with nothing in it is an empty array, which is a
         healthy state — not 0% of something, and not missing data. */
      cell.textContent = node.array_empty ? 'empty array' : '—';
      cell.className = 'um-unknown';
      return;
    }
    var pct = Math.round((cap.used / cap.total) * 100);
    var bar = document.createElement('span');
    bar.className = 'um-capbar';
    var fill = document.createElement('span');
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    cell.appendChild(bar);
    cell.appendChild(document.createTextNode(' ' + pct + '% · ' + bytes(cap.used)
      + ' of ' + bytes(cap.total)));
  }

  function render(nodes) {
    var body = document.getElementById('um-fleet-rows');
    body.textContent = '';

    if (!nodes.length) {
      var row = body.insertRow();
      row.insertCell().colSpan = 7;
      row.cells[0].textContent =
        'No nodes enrolled. Go to Settings → Utilities → Unraid-Manager to add one.';
      document.getElementById('um-summary').textContent = '';
      return;
    }

    var counts = {ok: 0, degraded: 0, unknown: 0};
    nodes.forEach(function (n) {
      counts[n.state] = (counts[n.state] || 0) + 1;
      var tr = body.insertRow();
      tr.insertCell().textContent = n.name;

      /* Colour, glyph and word together — never colour alone. */
      var state = tr.insertCell();
      state.textContent = STATE_LABEL[n.state] || STATE_LABEL.unknown;
      state.className = 'um-' + n.state;

      tr.insertCell().textContent = n.array_state || '—';
      capacityCell(tr.insertCell(), n);
      tr.insertCell().textContent = (n.unraid || '—') + ' / ' + (n.api || '—');

      var noti = tr.insertCell();
      if (n.unread) {
        noti.textContent = n.unread.alert + ' alert · ' + n.unread.warning
          + ' warn · ' + n.unread.info + ' info';
      } else {
        noti.textContent = '—';
        noti.className = 'um-unknown';
      }

      var seen = tr.insertCell();
      seen.textContent = n.last_seen || 'never';
      if (!n.last_seen) seen.className = 'um-unknown';

      tr.style.cursor = 'pointer';
      tr.addEventListener('click', function () { showDetail(n.id); });
    });

    document.getElementById('um-summary').textContent =
      nodes.length + ' node(s): ' + counts.ok + ' ok, ' + counts.degraded
      + ' degraded, ' + counts.unknown + ' unknown.';
  }

  function showDetail(id) {
    fetch(NODES + '?id=' + encodeURIComponent(id), {credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (n) {
        var box = document.getElementById('um-detail');
        box.textContent = '';
        var h = document.createElement('h3');
        h.textContent = n.name + ' — ' + (n.address || '') + ':' + (n.port || '');
        box.appendChild(h);
        var table = document.createElement('table');
        table.className = 'tablesorter';
        Object.keys(n.domains || {}).forEach(function (name) {
          var d = n.domains[name];
          var tr = table.insertRow();
          tr.insertCell().textContent = name;
          var st = tr.insertCell();
          st.textContent = STATE_LABEL[d.status] || (d.status === 'error' ? '⚠ Error' : '? Unknown');
          st.className = 'um-' + d.status;
          tr.insertCell().textContent = d.fetched_at || 'never';
          tr.insertCell().textContent = d.error || '';
        });
        box.appendChild(table);
      });
  }

  function refresh() {
    fetch(NODES, {credentials: 'same-origin'})
      .then(function (r) { return r.json(); })
      .then(function (r) {
        /* A 200 carrying {"error": …} is the API telling us it has no database
           to read — that is not a successful refresh, and treating it as one
           would hold the banner off while the numbers on screen go stale. */
        if (!r || !r.nodes) return;
        lastGood = Date.now();
        document.getElementById('um-stale').hidden = true;
        render(r.nodes);
      })
      .catch(function () { /* the staleness check below is the report */ });
  }

  function checkStale() {
    if (Date.now() - lastGood > STALE_MS) {
      var banner = document.getElementById('um-stale');
      banner.hidden = false;
      banner.textContent = 'These numbers are more than three minutes old — the manager '
        + 'has not answered since ' + new Date(lastGood).toLocaleTimeString()
        + '. Check that managerd is running on the Settings page.';
    }
  }

  /* nchan carries a nudge, not the data: on any message we re-fetch through the
     authenticated API. If the stream never connects, the timer below is the
     whole mechanism and the page still works. */
  try {
    var stream = new EventSource('/sub/unraid-manager');
    stream.onmessage = refresh;
    stream.onerror = function () { /* the fallback timer covers it */ };
  } catch (e) { /* no EventSource: fallback only */ }

  refresh();
  setInterval(refresh, FALLBACK_MS);
  setInterval(checkStale, 15000);
})();
