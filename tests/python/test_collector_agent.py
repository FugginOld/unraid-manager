import json
import unittest

import context
import agentclient
import collector
import smart


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
        # Envelope-level malformed: the whole reply is not even the
        # {device: raw text} map parse_smart expects, so there is no single
        # device to blame it on - `.items()` itself fails, and the domain as a
        # whole is what could not be read. This must still become `error`
        # (IMPORTANT 3): a wrong-shaped device is a different, per-device
        # failure now, tested separately below.
        got = self.collect(lambda node, verb, args, timeout: 'not even a device map')
        self.assertEqual('error', got.status)

    def test_one_devices_garbage_json_does_not_blank_the_whole_node(self):
        # IMPORTANT 3: a single device whose smartctl stdout is not JSON used
        # to raise out of parse_smart, and collect_agent's outer catch turned
        # the ENTIRE node's smart domain into `error` with payload None - 36
        # healthy drives lost because of one. scripts/agent-exec's own
        # doctrine: a single dead drive must not blank every healthy one
        # beside it.
        #
        # Completion pass: '/dev/sdd' is a scalar that IS valid JSON (a plain
        # int) and '/dev/sde' is a dict - the shape a peer agent sends the
        # already-parsed document in, plausible version skew per
        # agentclient.exec_response returning reply.get('data') unvalidated.
        # Both used to escape the ValueError-only guard: doc.pop() on an int
        # raises AttributeError, and json.loads() on a dict raises TypeError
        # before .pop is ever reached - either one still blanked the node.
        raw = context.fixture('agent-smart-golem-sda.json')
        got = self.collect(lambda node, verb, args, timeout: {
            '/dev/sda': raw, '/dev/sdb': raw, '/dev/sdc': 'not json',
            '/dev/sdd': '12345', '/dev/sde': {'smart_status': {'passed': True}},
        })
        self.assertEqual('ok', got.status)
        self.assertEqual('OK', got.payload['disks']['/dev/sda']['verdict'])
        self.assertEqual('OK', got.payload['disks']['/dev/sdb']['verdict'])
        self.assertEqual('UNKNOWN', got.payload['disks']['/dev/sdc']['verdict'])
        self.assertEqual('UNKNOWN', got.payload['disks']['/dev/sdd']['verdict'])
        self.assertEqual('UNKNOWN', got.payload['disks']['/dev/sde']['verdict'])
        self.assertEqual(5, got.payload['count'])

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
        doc = context.fixture_json('agent-smart-golem-sda.json')
        doc['serial_number'] = 'SENTINEL-SERIAL-NOT-FOR-EXPORT'
        doc['logical_unit_id'] = 'SENTINEL-LUN-NOT-FOR-EXPORT'
        got = self.collect(lambda node, verb, args, timeout:
                           {'/dev/sda': json.dumps(doc)})
        self.assertNotIn('SENTINEL', json.dumps(got.payload))


class TestParseSmart(unittest.TestCase):
    def test_garbage_json_is_unknown_with_its_own_reason_not_none_s(self):
        # A device that sent something unparseable is a different fact from a
        # device that sent nothing at all - smartctl DID run here, the manager
        # just could not parse what came back, so "smartctl could not read
        # this device" (the None/'' reason) would misattribute the failure.
        got = collector.parse_smart({'/dev/sda': 'not json'})
        disk = got['disks']['/dev/sda']
        self.assertEqual('UNKNOWN', disk['verdict'])
        self.assertNotEqual(smart.verdict(None)['reasons'], disk['reasons'])

    def test_an_empty_string_reads_the_same_as_none(self):
        # smartctl's exit status is a bitmask: a healthy read of a disk with
        # prefail attributes set exits non-zero, and the agent still sends the
        # full document in that case - it only sends '' when it truly could
        # not read the disk. '' and None are the same "no data" fact here -
        # not merely "both land on UNKNOWN", but the identical verdict dict,
        # reasons and summary included.
        got = collector.parse_smart({'/dev/sda': ''})
        self.assertEqual('UNKNOWN', got['disks']['/dev/sda']['verdict'])
        self.assertEqual(collector.parse_smart({'/dev/sda': None})['disks']['/dev/sda'],
                         got['disks']['/dev/sda'])

    def test_an_unrelated_field_survives_into_the_summary(self):
        # The committed fixtures were scrubbed on the box - they carry neither
        # serial_number nor logical_unit_id - so this dict is built here, in
        # the test, the way live smartctl output does. The strip itself is
        # unobservable from here: a key parse_smart drops and a key
        # smart.verdict() never reads produce the identical envelope, so that
        # guarantee is pinned by source inspection instead, in
        # tests/php/policy_test.php ('parse_smart strips serial_number and
        # logical_unit_id'). What IS observable at this level is that the
        # strip is not a wholesale wipe - an unrelated field still reaches the
        # summary the verdict carries.
        raw = json.dumps({
            'device': {'name': '/dev/sda'},
            'model_name': 'ST8000NM0055',
            'serial_number': 'ZA1ABCDE',
            'logical_unit_id': '0x5000c500a1b2c3d4',
            'smart_status': {'passed': True},
        })
        got = collector.parse_smart({'/dev/sda': raw})
        self.assertEqual('ST8000NM0055', got['disks']['/dev/sda']['summary']['model'])


if __name__ == '__main__':
    unittest.main()
