import json
import unittest

import context  # noqa: F401
import ctl

KEY = 'ctl-test-key-01234567890123456789012345678901'


def handlers():
    calls = []
    return calls, {
        'status': lambda args: {'uptime': 42, 'nodes': []},
        'poll_now': lambda args: calls.append(('poll_now', args.get('node_id'))) or {'scheduled': True},
        'reload': lambda args: {'added': [], 'removed': []},
        'test_node': lambda args: {'verdict': 'ok', 'echo_len': len(args.get('key', ''))},
        'boom': lambda args: (_ for _ in ()).throw(RuntimeError('handler exploded')),
    }


class TestHandle(unittest.TestCase):
    def setUp(self):
        self.calls, self.handlers = handlers()

    def reply(self, line):
        out = ctl.handle(line, self.handlers)
        self.assertTrue(out.endswith('\n'), 'every reply is one line')
        return json.loads(out)

    def test_status_returns_ok_with_the_handler_payload(self):
        got = self.reply('{"cmd":"status"}')
        self.assertTrue(got['ok'])
        self.assertEqual(42, got['uptime'])

    def test_args_reach_the_handler(self):
        self.reply('{"cmd":"poll_now","node_id":"a1b2"}')
        self.assertEqual([('poll_now', 'a1b2')], self.calls)

    def test_unknown_command_is_refused_by_name(self):
        got = self.reply('{"cmd":"rm_rf"}')
        self.assertFalse(got['ok'])
        self.assertIn('rm_rf', got['error'])

    def test_missing_cmd_is_refused(self):
        self.assertFalse(self.reply('{"node_id":"a"}')['ok'])

    def test_malformed_json_is_refused_not_raised(self):
        got = self.reply('{"cmd":')
        self.assertFalse(got['ok'])
        self.assertIn('json', got['error'].lower())

    def test_empty_line_is_refused(self):
        self.assertFalse(self.reply('')['ok'])

    def test_a_json_array_is_refused(self):
        self.assertFalse(self.reply('[1,2,3]')['ok'])

    def test_a_handler_exception_becomes_an_error_reply(self):
        # A blown handler must not take the daemon's listener down.
        got = self.reply('{"cmd":"boom"}')
        self.assertFalse(got['ok'])
        self.assertIn('exploded', got['error'])

    def test_reply_is_a_single_line_even_with_embedded_newlines(self):
        out = ctl.handle('{"cmd":"boom"}', {
            'boom': lambda args: (_ for _ in ()).throw(RuntimeError('line one\nline two'))})
        self.assertEqual(1, out.count('\n'))

    def test_test_node_passes_the_key_through_without_storing_it(self):
        got = self.reply('{"cmd":"test_node","address":"h","port":1,"key":"%s"}' % KEY)
        self.assertTrue(got['ok'])
        self.assertEqual(len(KEY), got['echo_len'])

    def test_test_node_by_id_carries_no_key_at_all(self):
        # The enrolled-node form: the daemon reads the key itself.
        got = self.reply('{"cmd":"test_node","node_id":"a1b2"}')
        self.assertTrue(got['ok'])
        self.assertEqual(0, got['echo_len'])

    def test_a_handler_that_refuses_returns_the_reason(self):
        # test_node with an unknown node_id raises ValueError in the daemon;
        # the socket turns that into an error reply, not a dead listener.
        out = ctl.handle('{"cmd":"t","node_id":"nosuch"}', {
            't': lambda args: (_ for _ in ()).throw(ValueError('no such node: nosuch'))})
        got = json.loads(out)
        self.assertFalse(got['ok'])
        self.assertIn('no such node', got['error'])

    def test_a_handler_reply_that_leaks_a_key_is_scrubbed(self):
        # Defence in depth: the socket is the last place a key could escape to
        # the PHP layer, and from there to a browser.
        out = ctl.handle('{"cmd":"t","key":"%s"}' % KEY,
                         {'t': lambda args: {'note': 'used %s' % args['key']}})
        self.assertNotIn(KEY, out)
        self.assertIn('<redacted>', out)


class TestPaths(unittest.TestCase):
    def test_socket_and_pid_live_under_var_run(self):
        self.assertEqual('/var/run/unraid-manager/managerd.sock', ctl.SOCKET_PATH)
        self.assertEqual('/var/run/unraid-manager/managerd.pid', ctl.PID_PATH)

    def test_nothing_under_boot(self):
        self.assertNotIn('/boot', ctl.SOCKET_PATH)
        self.assertNotIn('/boot', ctl.PID_PATH)


if __name__ == '__main__':
    unittest.main()
