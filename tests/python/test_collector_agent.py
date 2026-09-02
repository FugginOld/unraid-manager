import json
import unittest

import context
import agentclient
import collector


class TestCollectAgent(unittest.TestCase):
    NODE = {'id': 'n1', 'address': '10.0.0.1', 'port': 1, 'tier': 1}
    DOMAIN = property(lambda self: collector.DOMAINS['smart'])

    def collect(self, exec_fn):
        return collector.collect_agent(exec_fn, self.NODE, self.DOMAIN)

    def test_a_good_reply_is_ok_with_a_payload(self):
        raw = context.fixture('agent-smart-golem-sda.json')
        got = self.collect(lambda node, verb, args, timeout: {'/dev/sda': raw})
        self.assertEqual('ok', got.status)
        self.assertIn('/dev/sda', got.payload['disks'])

    def test_an_unreachable_agent_is_unknown_not_error(self):
        # Fail closed, and match the graphql path exactly: could not READ it is
        # unknown; answered and failed is error. A third convention on this path
        # would mean two vocabularies reaching one operator.
        def boom(node, verb, args, timeout):
            raise agentclient.AgentUnreachable('ssh exited 255: publickey')
        got = self.collect(boom)
        self.assertEqual('unknown', got.status)
        self.assertIsNone(got.payload)

    def test_a_missing_verb_says_so_and_is_not_an_error(self):
        # The third state. 'needs a newer agent' rendered as an error sends the
        # operator hunting a fault that is really a version gap.
        def old(node, verb, args, timeout):
            raise agentclient.VerbUnsupported('unknown verb')
        got = self.collect(old)
        self.assertEqual('unsupported', got.status)
        self.assertIn('newer agent', got.error)

    def test_a_refusal_is_an_error_with_its_reason(self):
        def refused(node, verb, args, timeout):
            raise agentclient.AgentRefused('not a device on this node: /dev/nope')
        got = self.collect(refused)
        self.assertEqual('error', got.status)
        self.assertIn('/dev/nope', got.error)

    def test_a_shape_we_do_not_understand_is_an_error_not_a_crash(self):
        got = self.collect(lambda node, verb, args, timeout: {'/dev/sda': 'not json'})
        self.assertEqual('error', got.status)

    def test_an_unreadable_disk_survives_as_unknown_not_as_absent(self):
        # The agent sends None for a drive it could not read. Dropping it here
        # would make a dead disk look like a disk that was never there.
        got = self.collect(lambda node, verb, args, timeout: {'/dev/sda': None})
        self.assertEqual('ok', got.status)
        self.assertEqual('UNKNOWN', got.payload['disks']['/dev/sda']['verdict'])

    def test_a_parsed_disk_carries_a_verdict_not_a_raw_document(self):
        raw = context.fixture('agent-smart-golem-sda.json')
        got = self.collect(lambda node, verb, args, timeout: {'/dev/sda': raw})
        disk = got.payload['disks']['/dev/sda']
        self.assertEqual('OK', disk['verdict'])
        self.assertEqual(55161, disk['summary']['power_on_hours'])
        # The raw document is gone. Storing 8 KB per device that no consumer
        # reads cost about 300 KB per node per poll.
        self.assertNotIn('scsi_error_counter_log', disk)

    def test_an_unreadable_disk_keeps_its_key_as_unknown(self):
        # Both None and the empty string mean "no data" - smartctl exits
        # non-zero on a healthy drive with prefail attributes set, so the agent
        # sends '' for a disk it read just fine. Dropping the key would make a
        # dead disk look like one that was never installed.
        got = self.collect(lambda node, verb, args, timeout:
                           {'/dev/sda': None, '/dev/sdb': ''})
        for device in ('/dev/sda', '/dev/sdb'):
            self.assertEqual('UNKNOWN',
                             got.payload['disks'][device]['verdict'], device)
        self.assertEqual(2, got.payload['count'])

    def test_no_serial_survives_into_the_payload(self):
        import json as _json
        doc = context.fixture_json('agent-smart-golem-sda.json')
        doc['serial_number'] = 'SENTINEL-SERIAL-NOT-FOR-EXPORT'
        doc['logical_unit_id'] = 'SENTINEL-LUN-NOT-FOR-EXPORT'
        got = self.collect(lambda node, verb, args, timeout:
                           {'/dev/sda': _json.dumps(doc)})
        self.assertNotIn('SENTINEL', _json.dumps(got.payload))


class TestParseSmart(unittest.TestCase):
    def test_an_empty_string_reads_the_same_as_none(self):
        # smartctl's exit status is a bitmask: a healthy read of a disk with
        # prefail attributes set exits non-zero, and the agent still sends the
        # full document in that case - it only sends '' when it truly could
        # not read the disk. '' and None are the same "no data" fact here.
        got = collector.parse_smart({'/dev/sda': ''})
        self.assertEqual('UNKNOWN', got['disks']['/dev/sda']['verdict'])

    def test_serial_number_and_logical_unit_id_are_stripped(self):
        # The committed fixtures were scrubbed on the box - they carry neither
        # field - so asserting against them would pass whether or not the
        # parser strips anything. This dict is built here, in the test, and
        # DOES carry both, the way live smartctl output does.
        raw = json.dumps({
            'device': {'name': '/dev/sda'},
            'model_name': 'ST8000NM0055',
            'serial_number': 'ZA1ABCDE',
            'logical_unit_id': '0x5000c500a1b2c3d4',
            'smart_status': {'passed': True},
        })
        got = collector.parse_smart({'/dev/sda': raw})
        disk = got['disks']['/dev/sda']
        self.assertNotIn('serial_number', disk)
        self.assertNotIn('logical_unit_id', disk)
        # and it is not a wholesale wipe - an unrelated field survives, by way
        # of the summary the verdict carries.
        self.assertEqual('ST8000NM0055', disk['summary']['model'])


if __name__ == '__main__':
    unittest.main()
