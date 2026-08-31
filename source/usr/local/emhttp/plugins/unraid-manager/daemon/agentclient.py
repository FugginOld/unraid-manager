"""The Tier 1 transport: ssh to a peer's forced command.

Shaped like gqlclient on purpose. `ssh_argv` and `exec_response` are pure, so
the whole classification story is testable on a machine with no ssh binary -
the same reason `parse_response` is testable with no socket.
"""
import json
import os
import subprocess

SSH_PORT = 22
# The handshake only. The overall call is bounded by the subprocess timeout,
# which the caller sizes against the agent's BUDGET + RUN_TIMEOUT (75s).
CONNECT_TIMEOUT = 10


class AgentUnreachable(Exception):
    """We could not get an answer out of the peer at all."""


class AgentRefused(Exception):
    """The agent answered, and said no."""


class VerbUnsupported(AgentRefused):
    """The agent answered and does not have that verb.

    A subclass because it IS a refusal, but the manager treats it as a third
    state: 'needs a newer agent' is not an error, and rendering it as one turns
    an ordinary version gap into a fault the operator chases.
    """


def exec_response(returncode, stdout, stderr):
    """Classify one ssh invocation. Pure."""
    text = (stdout or '').strip()
    if not text:
        return _unreachable(returncode, stderr)
    try:
        reply = json.loads(text)
    except Exception:
        # Not our agent: an MOTD, a banner, or a different forced command.
        return _unreachable(returncode, stderr or text[:200])
    if not isinstance(reply, dict) or 'ok' not in reply:
        return _unreachable(returncode, 'reply is not an agent envelope')
    if reply.get('ok'):
        if 'data' not in reply:
            # Absent vs empty: a missing key is "the agent said nothing", not
            # "the answer is empty" - conflating them turns silence into a
            # false "no results" for whatever reads it downstream.
            raise AgentRefused('the agent replied ok with no data')
        return reply.get('data')
    code = reply.get('code')
    message = (reply.get('error') or code or 'the agent refused')[:200]
    if code == 'UNKNOWN_VERB':
        raise VerbUnsupported(message)
    raise AgentRefused(message)


def _unreachable(returncode, detail):
    raise AgentUnreachable('ssh exited %s: %s'
                           % (returncode, (detail or 'no output').strip()[:200]))


def key_path(keys_dir, node_id):
    return os.path.join(keys_dir, node_id + '.ssh')


def known_hosts_path(keys_dir):
    return os.path.join(keys_dir, 'known_hosts')


def ssh_argv(node, keyfile, known_hosts, timeout):
    """The exact argv. Pure, so the whole option set is a test, not a habit."""
    return [
        'ssh',
        '-i', keyfile,
        '-p', str(SSH_PORT),
        '-o', 'BatchMode=yes',
        '-o', 'PasswordAuthentication=no',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'UserKnownHostsFile=' + known_hosts,
        # ConnectTimeout bounds the TCP handshake ONLY; the subprocess timeout
        # bounds the whole call. They are different jobs and must not share a
        # number: a 90s ConnectTimeout means a dead peer holds a worker for 90s
        # before anyone learns it is dead, while the enumeration it was sized
        # for never even starts.
        '-o', 'ConnectTimeout=%d' % CONNECT_TIMEOUT,
        '-o', 'ForwardAgent=no',
        '-o', 'ForwardX11=no',
        # -T only, never -N. A forced command replaces the command on an exec or
        # shell request; -N sends neither, so sshd has nothing to replace and
        # agent-exec never runs. The call would then sit until our own timeout
        # and report a healthy, correctly configured peer as unreachable.
        '-T',
        # `--` before the destination: an address is data. Without it an address
        # beginning with a dash is read by ssh as an option, and ProxyCommand is
        # an option that runs a program.
        '--',
        'root@' + str(node['address']),
    ]


def _run_ssh(argv, stdin_text, timeout):
    done = subprocess.run(argv, input=stdin_text, capture_output=True,
                          text=True, timeout=timeout)
    return done.returncode, done.stdout, done.stderr


def exec_agent(node, verb, args, timeout, keys_dir, run_fn=None):
    """Run one verb on one peer. Raises AgentUnreachable/AgentRefused."""
    run_fn = run_fn or _run_ssh
    envelope = json.dumps({'verb': verb, 'args': args or {}})
    argv = ssh_argv(node, key_path(keys_dir, node['id']),
                    known_hosts_path(keys_dir), timeout)
    try:
        returncode, stdout, stderr = run_fn(argv, envelope, timeout)
    except Exception as exc:                       # noqa: BLE001
        raise AgentUnreachable('%s: %s' % (type(exc).__name__, exc))
    return exec_response(returncode, stdout, stderr)
