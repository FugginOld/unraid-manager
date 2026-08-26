"""One GraphQL POST, and the classification of what came back.

Split deliberately: build_request and parse_response are pure, so every failure
shape observed on the real boxes -- a 504 HTML page from nginx, a resolver error
that nulled the whole response, a key without scope answering 200 -- is a unit
test against a captured fixture rather than something only a live box can show.

Nothing here logs. The caller logs, through scrub().
"""
import json
import ssl
import urllib.error
import urllib.request

REDACTED = '<redacted>'

# GraphQL-level codes that mean "your key is the problem", not "the resolver
# broke". They separate an enrollment mistake from a box-side fault in the
# probe report, which is the difference between two very different next steps
# for the operator.
AUTH_CODES = ('UNAUTHENTICATED', 'UNAUTHORIZED', 'FORBIDDEN', 'GRAPHQL_UNAUTHENTICATED')


class TransportError(Exception):
    """Could not get a GraphQL answer at all: unreachable, TLS, timeout, non-JSON."""


class AuthError(TransportError):
    """The endpoint answered, and rejected the key."""


class DomainError(Exception):
    """The endpoint answered with JSON, and the query failed."""


def scrub(text, secret):
    """Remove a key from anything about to be logged or returned."""
    if not secret:
        return text
    return str(text).replace(secret, REDACTED)


def build_request(address, port, api_key, query):
    url = 'https://%s:%d/graphql' % (address, int(port))
    body = json.dumps({'query': query}, separators=(',', ':')).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json')
    req.add_header('x-api-key', api_key)
    return req


def _tls_context():
    # Unraid serves a self-signed certificate on its LAN address; there is no CA
    # to verify against and no hostname that would match. The operator names the
    # peer and pastes its key by hand, which is where the trust actually comes
    # from. Pinning the cert fingerprint captured at enrollment is the P1
    # upgrade -- see the plan's Risks section.
    # ponytail: unverified TLS, pin per-node fingerprints at enrollment in P1.
    return ssl._create_unverified_context()


def parse_response(status, body):
    """Return the `data` object, or raise the reason we do not have one."""
    if not body:
        raise TransportError('HTTP %s with an empty body' % status)

    text = body.decode('utf-8', 'replace')
    try:
        doc = json.loads(text)
    except ValueError:
        # nginx's 504 page is HTML, and Query.disks produces it reproducibly on
        # Raven. Say so in terms the operator recognises rather than reporting
        # a JSON parse error.
        snippet = ' '.join(text.split())[:120]
        if status == 504 or 'Gateway Time-out' in text:
            raise TransportError(
                'HTTP 504 Gateway Time-out from nginx - the query took longer than the '
                'server allows (%s)' % snippet)
        raise TransportError('HTTP %s with a non-JSON body: %s' % (status, snippet))

    errors = doc.get('errors') or []
    messages = '; '.join(str(e.get('message', e)) for e in errors) or 'no message'
    codes = [str((e.get('extensions') or {}).get('code', '')).upper() for e in errors]

    if status in (401, 403) or any(c in AUTH_CODES for c in codes):
        raise AuthError('API key rejected (HTTP %s): %s' % (status, messages))
    if status >= 400:
        raise TransportError('HTTP %s: %s' % (status, messages))
    if errors:
        # Constraint 1: partial data with errors is not a usable answer.
        raise DomainError(messages)
    if doc.get('data') is None:
        raise DomainError('response carried no data and no error')
    return doc['data']


def post(address, port, api_key, query, timeout, opener=None):
    """POST one query. Raises TransportError / AuthError / DomainError."""
    req = build_request(address, port, api_key, query)
    opener = opener or urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_tls_context()))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return parse_response(getattr(resp, 'status', 200), resp.read())
    except urllib.error.HTTPError as exc:
        return parse_response(exc.code, exc.read())
    except (TransportError, DomainError):
        raise
    except Exception as exc:                      # noqa: BLE001 - socket/TLS/timeout family
        # str(exc) can quote the request; scrub before it reaches a log line.
        raise TransportError(scrub('%s: %s' % (type(exc).__name__, exc), api_key))
