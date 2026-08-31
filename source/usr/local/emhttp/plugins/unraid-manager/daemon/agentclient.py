"""The Tier 1 transport: ssh to a peer's forced command.

Shaped like gqlclient on purpose. `ssh_argv` and `exec_response` are pure, so
the whole classification story is testable on a machine with no ssh binary -
the same reason `parse_response` is testable with no socket.
"""
import json


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
