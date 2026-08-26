"""The control socket: how PHP asks the daemon for something.

One JSON object per line in, one JSON object per line out. The dispatch is a
pure function over a string so every malformed-input and blown-handler case is
a unit test; serve() is the thin socket shell around it, which is also why this
module is testable on a machine with no AF_UNIX at all.
"""
import json
import os
import socket
import stat

import gqlclient

RUN_DIR = '/var/run/unraid-manager'
SOCKET_PATH = RUN_DIR + '/managerd.sock'
PID_PATH = RUN_DIR + '/managerd.pid'

LOG_PATH = '/var/log/unraid-manager/managerd.log'


def _line(obj, secret=None):
    text = json.dumps(obj, separators=(',', ':'), default=str)
    if secret:
        text = gqlclient.scrub(text, secret)
    return text.replace('\n', ' ') + '\n'


def handle(line, handlers):
    """Dispatch one request line. Never raises."""
    try:
        request = json.loads(line)
    except (ValueError, TypeError):
        return _line({'ok': False, 'error': 'malformed json request'})

    if not isinstance(request, dict):
        return _line({'ok': False, 'error': 'request must be a json object'})

    cmd = request.get('cmd')
    if not cmd:
        return _line({'ok': False, 'error': 'no cmd in request'})

    handler = handlers.get(cmd)
    if handler is None:
        return _line({'ok': False, 'error': 'unknown command: %s' % cmd})

    # test_node carries a key the operator just typed. It is used and dropped --
    # never persisted, never logged -- and scrubbed out of the reply as a last
    # line of defence before it could reach a browser.
    secret = request.get('key')
    try:
        result = handler(request) or {}
    except Exception as exc:                      # noqa: BLE001 - one bad command must not end the daemon
        return _line({'ok': False, 'error': '%s: %s' % (type(exc).__name__, exc)}, secret)

    if not isinstance(result, dict):
        result = {'result': result}
    reply = {'ok': True}
    reply.update(result)
    return _line(reply, secret)


def serve(sock_path, handlers, stop_event, ready=None):
    """Listen on an AF_UNIX socket until stop_event is set.

    Mode 0600, root-owned: the only client is the webGUI's php-fpm, running as
    root on the same box. There is no authentication on this socket because
    reaching it already means being root.
    """
    os.makedirs(os.path.dirname(sock_path), exist_ok=True)
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    os.chmod(sock_path, stat.S_IRUSR | stat.S_IWUSR)
    server.listen(8)
    server.settimeout(0.5)
    if ready is not None:
        ready.set()

    try:
        while not stop_event.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(10.0)
                buf = b''
                while b'\n' not in buf and len(buf) < 65536:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                request = buf.split(b'\n', 1)[0].decode('utf-8', 'replace')
                conn.sendall(handle(request, handlers).encode('utf-8'))
            except OSError:
                pass
            finally:
                conn.close()
    finally:
        server.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass
