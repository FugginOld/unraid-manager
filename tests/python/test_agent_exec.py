import io
import json
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


if __name__ == '__main__':
    unittest.main()
