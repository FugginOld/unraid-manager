import json
import unittest

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

    def test_an_unknown_verb_is_refused_by_code_not_by_message(self):
        got = self.reply('{"verb": "rm.everything"}')
        self.assertFalse(got['ok'])
        self.assertEqual('UNKNOWN_VERB', got['code'])

    def test_a_refused_verb_runs_nothing(self):
        ran = []
        table = {'safe': (lambda a: {}, lambda a: ran.append('safe'))}
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
        # With a forced command, that variable holds whatever the client typed.
        # Reading it at all is the bug; this asserts the source does not.
        with open(context.AGENT, encoding='utf-8') as fh:
            self.assertNotIn('SSH_ORIGINAL_COMMAND', fh.read())


if __name__ == '__main__':
    unittest.main()
