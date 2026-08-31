import json
import unittest

import context                                     # noqa: F401
import agentclient


class TestExecResponse(unittest.TestCase):
    def test_a_good_reply_yields_its_data(self):
        got = agentclient.exec_response(
            0, '{"ok": true, "verb": "agent.hello", "data": {"version": "1"}}', '')
        self.assertEqual({'version': '1'}, got)

    def test_an_unknown_verb_is_its_own_exception(self):
        # NOT an error. The manager must be able to tell "this agent is older
        # than I am" from "the command failed", or a missing feature and a real
        # fault arrive on the same channel.
        with self.assertRaises(agentclient.VerbUnsupported):
            agentclient.exec_response(
                1, '{"ok": false, "verb": "pool.balance", "code": "UNKNOWN_VERB",'
                   ' "error": "unknown verb"}', '')

    def test_a_failed_verb_is_a_refusal_with_its_reason(self):
        with self.assertRaises(agentclient.AgentRefused) as caught:
            agentclient.exec_response(
                1, '{"ok": false, "verb": "smart.attributes", "code": "BAD_ARGS",'
                   ' "error": "not a device on this node: /dev/nope"}', '')
        self.assertIn('/dev/nope', str(caught.exception))

    def test_ssh_failing_to_connect_is_unreachable_not_refused(self):
        # 255 is ssh's own failure, not the agent's. Refused means the peer
        # answered; unreachable means it did not, and they are different facts.
        with self.assertRaises(agentclient.AgentUnreachable):
            agentclient.exec_response(255, '', 'ssh: connect to host ... refused')

    def test_no_reply_at_all_is_unreachable(self):
        with self.assertRaises(agentclient.AgentUnreachable):
            agentclient.exec_response(0, '', '')

    def test_a_reply_that_is_not_json_is_unreachable_not_a_crash(self):
        # An MOTD, a shell banner, or a forced command that is not our agent.
        with self.assertRaises(agentclient.AgentUnreachable):
            agentclient.exec_response(0, 'Welcome to Unraid!\n', '')

    def test_stderr_never_reaches_the_caller_as_data(self):
        with self.assertRaises(agentclient.AgentUnreachable) as caught:
            agentclient.exec_response(255, '', 'Permission denied (publickey).')
        self.assertIn('publickey', str(caught.exception))

    def test_ok_with_no_data_key_at_all_is_a_refusal_not_empty_data(self):
        # "the agent said nothing" must not silently become "empty result" -
        # a caller doing (data or {}).items() cannot tell the two apart once
        # None and {} both arrive labelled success.
        with self.assertRaises(agentclient.AgentRefused):
            agentclient.exec_response(
                0, '{"ok": true, "verb": "smart.attributes"}', '')

    def test_ok_with_data_present_but_empty_still_returns_it(self):
        # The other half of the same distinction: a PRESENT but falsy data
        # value ({}) is a legitimate answer and must not be mistaken for the
        # missing-key case above.
        got = agentclient.exec_response(
            0, '{"ok": true, "verb": "smart.attributes", "data": {}}', '')
        self.assertEqual({}, got)

    def test_a_refusal_message_is_bounded_in_length(self):
        huge = 'x' * 10000
        with self.assertRaises(agentclient.AgentRefused) as caught:
            agentclient.exec_response(
                1, json.dumps({'ok': False, 'verb': 'smart.attributes',
                               'code': 'BAD_ARGS', 'error': huge}), '')
        self.assertLess(len(str(caught.exception)), 300)


class TestSshArgv(unittest.TestCase):
    NODE = {'id': 'n1', 'address': '192.168.2.248', 'port': 15137}

    def argv(self, node=None, timeout=30):
        return agentclient.ssh_argv(node or self.NODE, '/k/n1.ssh', '/k/known_hosts',
                                    timeout)

    def test_the_address_is_an_argv_element_not_a_string(self):
        # No shell anywhere in the action path. An address is data, not syntax.
        self.assertIn('root@192.168.2.248', self.argv())

    def test_it_never_prompts_for_anything(self):
        argv = ' '.join(self.argv())
        for option in ('BatchMode=yes', 'PasswordAuthentication=no',
                       'StrictHostKeyChecking=yes'):
            self.assertIn(option, argv, option)

    def test_it_grants_the_peer_nothing(self):
        argv = ' '.join(self.argv())
        for option in ('-N', 'ForwardAgent=no', 'ForwardX11=no'):
            self.assertIn(option, argv, option)

    def test_the_ssh_port_is_22_not_the_graphql_port(self):
        # node['port'] is the GraphQL API port. Sending ssh there talks to the
        # API and hangs - a mix-up that would look like an unreachable agent.
        # Checking '15137' is absent is not enough on its own - it would also
        # pass if -p were dropped entirely. Pin the actual flag pair too, so a
        # swap to node['port'] (still present as '-p', '15137') fails here.
        argv = self.argv()
        self.assertNotIn('15137', ' '.join(argv))
        self.assertIn('-p', argv)
        self.assertEqual('22', argv[argv.index('-p') + 1])

    def test_connect_timeout_is_fixed_not_the_call_timeout(self):
        # ConnectTimeout bounds the TCP handshake only; it must never track the
        # caller's subprocess timeout, or a dead peer holds a worker for as
        # long as that timeout instead of failing fast at the handshake.
        argv = ' '.join(self.argv(timeout=9999))
        self.assertIn('ConnectTimeout=10', argv)
        self.assertNotIn('ConnectTimeout=9999', argv)

    def test_an_address_that_looks_like_an_option_is_still_an_address(self):
        argv = self.argv({'id': 'n1', 'address': '-oProxyCommand=touch /tmp/x',
                          'port': 1})
        self.assertIn('--', argv)
        self.assertLess(argv.index('--'), argv.index('root@-oProxyCommand=touch /tmp/x'))


class TestExecAgent(unittest.TestCase):
    NODE = {'id': 'n1', 'address': '10.0.0.1', 'port': 1}

    def test_the_envelope_goes_in_on_stdin(self):
        seen = {}

        def fake(argv, stdin_text, timeout):
            seen['argv'], seen['stdin'] = argv, stdin_text
            return 0, '{"ok": true, "verb": "agent.hello", "data": {"v": 1}}', ''

        got = agentclient.exec_agent(self.NODE, 'agent.hello', {}, 30, '/k', run_fn=fake)
        self.assertEqual({'v': 1}, got)
        self.assertEqual({'verb': 'agent.hello', 'args': {}}, json.loads(seen['stdin']))

    def test_no_argument_ever_reaches_the_command_line(self):
        # Arguments travel in the stdin envelope, never as argv. This is what
        # makes a hostile argument a parsing problem rather than a shell one.
        seen = {}

        def fake(argv, stdin_text, timeout):
            seen['argv'] = argv
            return 0, '{"ok": true, "verb": "v", "data": {}}', ''

        agentclient.exec_agent(self.NODE, 'v', {'devices': ['/dev/sda']}, 30, '/k',
                               run_fn=fake)
        self.assertNotIn('/dev/sda', ' '.join(seen['argv']))

    def test_a_timeout_is_unreachable(self):
        def fake(argv, stdin_text, timeout):
            raise TimeoutError('timed out')

        with self.assertRaises(agentclient.AgentUnreachable):
            agentclient.exec_agent(self.NODE, 'agent.hello', {}, 1, '/k', run_fn=fake)
