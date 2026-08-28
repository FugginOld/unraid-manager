/* Settings page behaviour. No framework, no build step — P0 is plain PHP and
   plain JS; the Svelte pane arrives in P1.
   Every POST carries Unraid's CSRF token, and the API key is read from its
   field, sent once, and never written back into the DOM. */
(function () {
  var root = document.getElementById('um-settings');
  if (!root) return;
  var CSRF = root.getAttribute('data-csrf');
  var NODES = '/plugins/unraid-manager/api/nodes.php';
  var SETTINGS = '/plugins/unraid-manager/api/settings.php';

  function post(url, fields) {
    var body = new URLSearchParams();
    body.set('csrf_token', CSRF);
    Object.keys(fields).forEach(function (k) { body.set(k, fields[k]); });
    return fetch(url, {method: 'POST', body: body, credentials: 'same-origin'})
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }

  function get(url) {
    return fetch(url, {credentials: 'same-origin'}).then(function (r) { return r.json(); });
  }

  function text(el, message, kind) {
    el.textContent = message || '';
    el.className = 'um-msg' + (kind ? ' um-' + kind : '');
  }

  /* ── settings ─────────────────────────────────────────────────────────── */
  var msg = document.getElementById('um-settings-msg');

  function loadSettings() {
    get(SETTINGS).then(function (s) {
      document.getElementById('um-db-path').value = s.db_path || '';
      document.getElementById('um-poll-fast').value = s.poll_fast;
      document.getElementById('um-poll-slow').value = s.poll_slow;
      /* An explicit override goes in the box; a blank box shows what it would
         inherit as a placeholder, so "blank" is never a mystery (F-8). */
      ['capacity_watch', 'capacity_high_water', 'temp_warn', 'temp_crit']
        .forEach(function (key) {
          var el = document.getElementById('um-' + key.replace(/_/g, '-'));
          var override = (s.overrides || {})[key];
          el.value = override === null || override === undefined ? '' : override;
          el.placeholder = (s.inherited || {})[key];
        });
      document.getElementById('um-error-window-min').value = s.error_window_min;
      var d = document.getElementById('um-daemon-status');
      if (s.daemon && s.daemon.ok) {
        d.textContent = 'Running. Up ' + s.daemon.uptime + 's, watching '
          + (s.daemon.nodes || []).length + ' node(s).'
          + (s.daemon.publishing ? '' : ' Live updates unavailable — the page will poll instead.');
      } else {
        d.textContent = 'Not running. ' + ((s.daemon && s.daemon.error) || '');
      }
    });
  }

  document.getElementById('um-save-settings').addEventListener('click', function () {
    text(msg, 'Saving…');
    post(SETTINGS, {
      db_path: document.getElementById('um-db-path').value,
      poll_fast: document.getElementById('um-poll-fast').value,
      poll_slow: document.getElementById('um-poll-slow').value,
      capacity_watch: document.getElementById('um-capacity-watch').value,
      capacity_high_water: document.getElementById('um-capacity-high-water').value,
      temp_warn: document.getElementById('um-temp-warn').value,
      temp_crit: document.getElementById('um-temp-crit').value,
      error_window_min: document.getElementById('um-error-window-min').value
    }).then(function (r) {
      if (r.error) { text(msg, r.error, 'bad'); return; }
      text(msg, 'Saved.', 'good');
      loadSettings();
    });
  });

  /* ── daemon controls ──────────────────────────────────────────────────── */
  var daemonMsg = document.getElementById('um-daemon-msg');
  ['start', 'stop', 'restart'].forEach(function (verb) {
    document.getElementById('um-daemon-' + verb).addEventListener('click', function () {
      text(daemonMsg, verb + 'ing…');
      post(SETTINGS, {daemon: verb}).then(function (r) {
        /* The rc script's own output is the explanation — a refused db_path
           arrives here as the sentence that names the flash drive. */
        text(daemonMsg, r.output || (r.ok ? 'done' : (r.error || 'failed')),
             r.ok ? 'good' : 'bad');
        loadSettings();
      });
    });
  });

  /* ── node list ────────────────────────────────────────────────────────── */
  function renderNodes(nodes) {
    var body = document.getElementById('um-node-rows');
    body.textContent = '';
    if (!nodes.length) {
      var empty = body.insertRow();
      empty.insertCell().colSpan = 5;
      empty.cells[0].textContent = 'No nodes enrolled yet. Add one below.';
      return;
    }
    nodes.forEach(function (n) {
      var tr = body.insertRow();
      tr.insertCell().textContent = n.name;
      tr.insertCell().textContent = n.address + ':' + n.port;
      tr.insertCell().textContent = n.has_key ? 'stored' : 'MISSING';
      tr.insertCell().textContent = n.last_seen || 'never';
      var actions = tr.insertCell();
      var test = document.createElement('button');
      test.type = 'button';
      test.textContent = 'Test';
      test.addEventListener('click', function () {
        /* No key is sent: the daemon reads this node's key from flash itself. */
        post(NODES, {action: 'test', id: n.id}).then(function (r) {
          if (!r.ok && r.error) { text(enrollMsg, r.error, 'bad'); return; }
          renderProbe(r);
        });
      });
      var poll = document.createElement('button');
      poll.type = 'button';
      poll.textContent = 'Poll now';
      poll.addEventListener('click', function () { post(NODES, {action: 'poll', id: n.id}); });
      var remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Remove';
      remove.addEventListener('click', function () {
        if (!window.confirm('Remove ' + n.name + '? Its key file and stored history go with it.')) return;
        post(NODES, {action: 'delete', id: n.id}).then(loadNodes);
      });
      actions.appendChild(test);
      actions.appendChild(poll);
      actions.appendChild(remove);
    });
  }

  function loadNodes() { get(NODES).then(function (r) { renderNodes(r.nodes || []); }); }

  /* ── probe and enroll ─────────────────────────────────────────────────── */
  var enrollBtn = document.getElementById('um-enroll');
  var enrollMsg = document.getElementById('um-enroll-msg');
  /* The Name field promises "taken from the node if left blank", and the probe
     is the only thing that knows what the node calls itself. Captured here so
     enroll can keep that promise; the server's own fallback is the address,
     which is what a fleet table full of IP addresses looks like. */
  var probedHostname = null;

  function renderProbe(report) {
    var box = document.getElementById('um-probe-report');
    box.textContent = '';
    var head = document.createElement('p');
    var h = report.headline || {};
    head.textContent = ({
      ok: 'Reachable and fully readable.',
      partial: 'Reachable, but this key cannot read everything.',
      bad_key: 'The node answered and rejected the key.',
      unreachable: 'Could not reach the node.'
    })[report.verdict] || 'Unknown result.';
    if (h.hostname) {
      head.textContent += ' ' + h.hostname + ' — Unraid ' + (h.unraid || '?')
        + ', API ' + (h.api || '?') + ', array ' + (h.array_state || '?')
        + (h.array_empty ? ' (empty)' : '') + ', ' + (h.shares === null ? '?' : h.shares) + ' shares.';
    }
    box.appendChild(head);

    var table = document.createElement('table');
    table.className = 'tablesorter';
    Object.keys(report.domains || {}).forEach(function (name) {
      var d = report.domains[name];
      var tr = table.insertRow();
      tr.insertCell().textContent = name;
      /* Colour is never the only carrier: glyph plus word, every time. */
      var glyph = d.status === 'ok' ? '✓ ok' : (d.status === 'error' ? '⚠ error' : '? unknown');
      var cell = tr.insertCell();
      cell.textContent = glyph;
      cell.className = 'um-' + d.status;
      tr.insertCell().textContent = d.error || '';
    });
    box.appendChild(table);
  }

  document.getElementById('um-probe').addEventListener('click', function () {
    text(enrollMsg, 'Probing…');
    enrollBtn.disabled = true;
    post(NODES, {
      action: 'probe',
      address: document.getElementById('um-address').value,
      port: document.getElementById('um-port').value,
      key: document.getElementById('um-key').value
    }).then(function (r) {
      if (!r.ok && r.error) { text(enrollMsg, r.error, 'bad'); return; }
      renderProbe(r);
      probedHostname = (r.headline && r.headline.hostname) || null;
      /* Enroll only after the node has proven it can answer with this key.
         Partial counts: a read-scoped key that cannot see one domain is still
         a node worth watching, and the report says which. */
      var usable = r.verdict === 'ok' || r.verdict === 'partial';
      enrollBtn.disabled = !usable;
      text(enrollMsg, usable ? 'Ready to enroll.' : 'Fix the address or key and probe again.',
           usable ? 'good' : 'bad');
    });
  });

  enrollBtn.addEventListener('click', function () {
    text(enrollMsg, 'Enrolling…');
    post(NODES, {
      action: 'enroll',
      name: document.getElementById('um-name').value || probedHostname || '',
      address: document.getElementById('um-address').value,
      port: document.getElementById('um-port').value,
      key: document.getElementById('um-key').value
    }).then(function (r) {
      if (r.error) { text(enrollMsg, r.error, 'bad'); return; }
      text(enrollMsg, 'Enrolled.', 'good');
      /* Clear the key from the DOM the moment it is no longer needed. */
      document.getElementById('um-key').value = '';
      document.getElementById('um-address').value = '';
      document.getElementById('um-name').value = '';
      probedHostname = null;
      enrollBtn.disabled = true;
      document.getElementById('um-probe-report').textContent = '';
      loadNodes();
    });
  });

  loadSettings();
  loadNodes();
})();
