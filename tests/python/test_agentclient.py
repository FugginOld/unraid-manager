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
