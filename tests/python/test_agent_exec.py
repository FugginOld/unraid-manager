import io
import json
import os
import unittest
from unittest import mock

import context

agent = context.load_agent()


class TestEnvelope(unittest.TestCase):
    def reply(self, text, table=None):
        return json.loads(agent.handle(text, table or agent.VERBS))

    def test_hello_reports_its_version_and_hostname(self):
        got = self.reply('{"verb": "agent.hello"}')
        self.assertTrue(got['ok'])
        self.assertEqual(agent.AGENT_VERSION, got['data']['version'])
        self.assertTrue(got['data']['hostname'])

    def test_hello_declares_every_verb_it_supports(self):
        # The version-skew contract: the manager never sends a verb the peer
        # did not claim. A hello that under-reports silently disables a feature;
        # one that over-reports turns a missing feature into a hard error.
        got = self.reply('{"verb": "agent.hello"}')
        self.assertEqual(sorted(agent.VERBS), sorted(got['data']['verbs']))

    def test_hello_reports_the_table_it_was_called_with_not_the_global(self):
        # hello(args, table) must read the table handle() dispatched through,
        # not the module-global VERBS - otherwise a restricted table (a peer
        # given a subset of verbs) over-reports what it actually supports.
        table = {'agent.hello': agent.VERBS['agent.hello'],
                 'x': (lambda a: {}, lambda a, t: {})}
        got = self.reply('{"verb": "agent.hello"}', table)
        self.assertIn('x', got['data']['verbs'])

    def test_an_unknown_verb_is_refused_by_code_not_by_message(self):
        got = self.reply('{"verb": "rm.everything"}')
        self.assertFalse(got['ok'])
        self.assertEqual('UNKNOWN_VERB', got['code'])

    def test_a_refused_verb_runs_nothing(self):
        ran = []
        table = {'safe': (lambda a: {}, lambda a, t: ran.append('safe'))}
        self.reply('{"verb": "nope"}', table)
        self.assertEqual([], ran)

    def test_garbage_on_stdin_is_an_envelope_error_not_a_crash(self):
        for text in ('', 'not json', '[]', 'null', '{"no_verb": 1}'):
            got = self.reply(text)
            self.assertFalse(got['ok'], text)
            self.assertEqual('BAD_ENVELOPE', got['code'], text)

    def test_the_reply_is_one_line(self):
        # The manager reads one JSON object from stdout. A pretty-printed reply
        # would still parse, but a verb that later streams would not.
        self.assertNotIn('\n', agent.handle('{"verb": "agent.hello"}', agent.VERBS))

    def test_ssh_original_command_is_never_read(self):
        # With a forced command, whatever the client typed arrives in an env
        # var set by sshd. Reading it at all is the bug; this asserts the
        # source does not. A concatenated spelling would dodge a
        # literal-string grep, so also rule out the os.environ / os.getenv
        # access it would take to read it.
        with open(context.AGENT, encoding='utf-8') as fh:
            source = fh.read()
        self.assertNotIn('SSH_ORIGINAL_COMMAND', source)
        self.assertNotIn('environ', source)
        self.assertNotIn('getenv', source)

    def test_a_verb_taking_no_args_rejects_any_via_valueerror(self):
        # Exercises the `except ValueError` arm specifically (as opposed to
        # the broader `except Exception` arm below it): _no_args raises
        # ValueError, not some other exception type, when given args.
        got = self.reply('{"verb": "agent.hello", "args": {"x": 1}}')
        self.assertFalse(got['ok'])
        self.assertEqual('BAD_ARGS', got['code'])
        self.assertEqual('this verb takes no arguments', got['error'])

    def test_a_validator_raising_anything_but_valueerror_still_yields_bad_args(self):
        # validate() does real work (Task 2 reads /sys/block in one). An
        # OSError escaping here must not escape handle() itself - that would
        # leave the peer answering a traceback on stderr and nothing on
        # stdout, which the manager can't tell apart from a dead connection.
        def _boom(a):
            raise TypeError('boom')
        table = {'v': (_boom, lambda a, t: {})}
        got = self.reply('{"verb": "v"}', table)
        self.assertFalse(got['ok'])
        self.assertEqual('BAD_ARGS', got['code'])

    def test_non_object_args_is_bad_args(self):
        got = self.reply('{"verb": "agent.hello", "args": [1, 2]}')
        self.assertFalse(got['ok'])
        self.assertEqual('BAD_ARGS', got['code'])

    def test_falsy_non_dict_args_are_bad_args_too(self):
        # `envelope.get('args') or {}` coerced every *falsy* non-dict value -
        # not just a populated list like [1, 2] above - into an empty dict,
        # so {"args": []}, {"args": 0}, {"args": false} and {"args": ""} all
        # silently passed validation as ok:true. A populated list alone does
        # not discriminate the fix; these falsy shapes do.
        for bad in ([], 0, False, ""):
            got = self.reply(json.dumps({'verb': 'agent.hello', 'args': bad}))
            self.assertFalse(got['ok'], bad)
            self.assertEqual('BAD_ARGS', got['code'], bad)

    def test_null_or_absent_args_still_defaults_to_empty(self):
        got = self.reply('{"verb": "agent.hello", "args": null}')
        self.assertTrue(got['ok'])
        got = self.reply('{"verb": "agent.hello"}')
        self.assertTrue(got['ok'])

    def test_a_run_that_raises_is_run_failed(self):
        def _boom(a, t):
            raise RuntimeError('boom')
        table = {'v': (lambda a: {}, _boom)}
        got = self.reply('{"verb": "v"}', table)
        self.assertFalse(got['ok'])
        self.assertEqual('RUN_FAILED', got['code'])

    def test_main_returns_zero_for_a_good_verb(self):
        with mock.patch('sys.stdin', io.StringIO('{"verb": "agent.hello"}')), \
                mock.patch('sys.stdout', io.StringIO()):
            self.assertEqual(0, agent.main())

    def test_main_returns_nonzero_for_an_unknown_verb(self):
        with mock.patch('sys.stdin', io.StringIO('{"verb": "rm.everything"}')), \
                mock.patch('sys.stdout', io.StringIO()):
            self.assertNotEqual(0, agent.main())


class TestReadVerbs(unittest.TestCase):
    def reply(self, text):
        return json.loads(agent.handle(text, agent.VERBS))

    def _patch(self, name, value):
        """Set a module attribute and restore it after the test, whatever happens."""
        original = getattr(agent, name)
        setattr(agent, name, value)
        self.addCleanup(setattr, agent, name, original)

    def test_a_device_that_is_not_ours_is_refused(self):
        # Validated against the agent's OWN enumeration, not a pattern. A regex
        # still admits a device that is not there, and smartctl on it hangs.
        self._patch('devices', lambda: ['/dev/sda'])
        got = self.reply('{"verb": "smart.attributes",'
                         ' "args": {"devices": ["/dev/nope"]}}')
        self.assertFalse(got['ok'])
        self.assertEqual('BAD_ARGS', got['code'])

        # An unexpected key alongside a valid devices list is refused too,
        # and the message names the offending key. 'zork', not 'x' - 'x'
        # is a substring of "unexpected" and would pass for the wrong reason.
        got = self.reply(json.dumps({'verb': 'smart.attributes',
                                     'args': {'devices': ['/dev/sda'], 'zork': 1}}))
        self.assertFalse(got['ok'])
        self.assertEqual('BAD_ARGS', got['code'])
        self.assertIn('zork', got['error'])

        # A single device given as a bare string, not a one-item list. Without
        # the isinstance guard this falls through to the membership check,
        # which iterates the string as characters and still ends up BAD_ARGS
        # for the wrong reason - so pin the message, not just the code.
        got = self.reply(json.dumps({'verb': 'smart.attributes',
                                     'args': {'devices': '/dev/sda'}}))
        self.assertFalse(got['ok'])
        self.assertEqual('BAD_ARGS', got['code'])
        self.assertIn('devices must be a list', got['error'])

    def test_a_traversal_argument_is_refused_the_same_way(self):
        self._patch('devices', lambda: ['/dev/sda'])
        for bad in ('/etc/shadow', '../../etc/shadow', '/dev/sda; rm -rf /',
                    '/dev/sda\n/dev/sdb'):
            got = self.reply(json.dumps({'verb': 'smart.attributes',
                                         'args': {'devices': [bad]}}))
            self.assertFalse(got['ok'], bad)
            self.assertEqual('BAD_ARGS', got['code'], bad)

    def test_no_devices_argument_means_all_of_them(self):
        # One call per node per cycle, never one per disk: Golem has 22, and a
        # per-device verb would open 22 SSH connections every slow cycle.
        self._patch('devices', lambda: ['/dev/sda', '/dev/sdb'])
        self._patch('_run', lambda argv: 'ran ' + argv[-1])
        got = self.reply('{"verb": "smart.attributes"}')
        self.assertEqual(['/dev/sda', '/dev/sdb'], sorted(got['data']))

    def test_one_unreadable_disk_does_not_lose_the_others(self):
        # Fail closed per device, never per node: a single dead drive must not
        # blank the SMART view of every healthy one beside it.
        self._patch('devices', lambda: ['/dev/sda', '/dev/sdb'])

        def boom(argv):
            if argv[-1] == '/dev/sda':
                raise OSError('I/O error')
            return 'fine'
        self._patch('_run', boom)
        got = self.reply('{"verb": "smart.attributes"}')
        self.assertTrue(got['ok'])
        self.assertIsNone(got['data']['/dev/sda'])
        self.assertEqual('fine', got['data']['/dev/sdb'])

    def test_smart_budget_stops_reading_further_devices(self):
        # One SSH call carries the whole enumeration; the aggregate BUDGET,
        # not the per-command RUN_TIMEOUT, is what keeps a 22-disk box inside
        # the transport's window. A fake clock advances the budget past its
        # limit after the first device - a test that actually sleeps 60s to
        # exercise this is not acceptable.
        self._patch('devices', lambda: ['/dev/sda', '/dev/sdb', '/dev/sdc'])
        clock = [0.0]

        class FakeClock:
            @staticmethod
            def monotonic():
                return clock[0]
        self._patch('time', FakeClock())

        def run(argv):
            clock[0] = agent.BUDGET + 1  # the budget is spent reading device one
            return 'data ' + argv[-1]
        self._patch('_run', run)

        got = self.reply('{"verb": "smart.attributes"}')
        self.assertTrue(got['ok'])
        self.assertEqual('data /dev/sda', got['data']['/dev/sda'])
        self.assertIsNone(got['data']['/dev/sdb'])
        self.assertIsNone(got['data']['/dev/sdc'])

    def test_mounts_are_returned_raw(self):
        # Raw on purpose: a parse bug in the agent costs twenty paste sessions,
        # in the manager it costs one file patch.
        #
        # Pointed at a temp file, never the host's /proc: no test may read the
        # real one, and on the dev machine it does not exist at all.
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.mounts', delete=False,
                                         newline='') as fh:
            fh.write('/dev/sda1 /mnt/disk1 xfs rw 0 0\n')
            path = fh.name
        self.addCleanup(os.unlink, path)
        self._patch('PROC_MOUNTS', path)

        got = self.reply('{"verb": "mounts.list"}')
        self.assertTrue(got['ok'])
        self.assertIn('/mnt/disk1', got['data']['proc_mounts'])

    def test_pool_balance_walks_btrfs_mounts_and_reports_zfs_absence(self):
        # Three branches, pinned in one test: the mount-walk filter (xfs is
        # not btrfs and must not appear), a per-pool failure that does not
        # blank the pool beside it, and a missing zpool binary reported as
        # None rather than as an error.
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.mounts', delete=False,
                                         newline='') as fh:
            fh.write('/dev/sda1 /mnt/xfsdisk xfs rw 0 0\n'
                     '/dev/sdb1 /mnt/pool1 btrfs rw 0 0\n'
                     '/dev/sdc1 /mnt/pool2 btrfs rw 0 0\n')
            path = fh.name
        self.addCleanup(os.unlink, path)
        self._patch('PROC_MOUNTS', path)

        def run(argv):
            if argv[0] == 'zpool':
                raise FileNotFoundError('no zpool binary')
            if argv[-1] == '/mnt/pool2':
                raise OSError('I/O error')
            return 'usage: ' + argv[-1]
        self._patch('_run', run)

        got = self.reply('{"verb": "pool.balance"}')
        self.assertTrue(got['ok'])
        self.assertEqual(
            {'btrfs': {'/mnt/pool1': 'usage: /mnt/pool1', '/mnt/pool2': None},
             'zfs': None},
            got['data'])

    def test_every_verb_is_declared_by_hello(self):
        got = self.reply('{"verb": "agent.hello"}')
        for verb in ('smart.attributes', 'mounts.list', 'pool.balance'):
            self.assertIn(verb, got['data']['verbs'])

    def test_no_verb_can_write(self):
        # The structural guarantee of P2a, asserted on the agent side. Task 5
        # asserts the same thing on the manager's domain table.
        with open(context.AGENT, encoding='utf-8') as fh:
            source = fh.read()
        for forbidden in ('umount', 'mkfs', 'shutdown', 'reboot',
                          "'w'", '"w"', 'os.remove', 'shutil',
                          'os.rename', 'os.mkdir', "'wb'", "mode='a'",
                          'os.unlink', 'os.makedirs', 'pathlib', 'write_text'):
            self.assertNotIn(forbidden, source, forbidden)


if __name__ == '__main__':
    unittest.main()
