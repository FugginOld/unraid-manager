import unittest

import context  # noqa: F401
import health

OK, WATCH, WARN, UNKNOWN = health.OK, health.WATCH, health.WARN, health.UNKNOWN


def run(current, proposals, up=2, down=5):
    """Feed a sequence of proposals in and return the state after each."""
    pending_state, pending_count = None, 0
    seen = []
    for proposed in proposals:
        current, pending_state, pending_count = health.apply_hysteresis(
            current, proposed, pending_state, pending_count, up=up, down=down)
        seen.append(current)
    return seen


class TestEscalation(unittest.TestCase):
    def test_one_bad_sample_does_not_flip(self):
        self.assertEqual([OK], run(OK, [WARN]))

    def test_two_agreeing_samples_escalate(self):
        self.assertEqual([OK, WARN], run(OK, [WARN, WARN]))

    def test_a_disagreeing_sample_resets_the_count(self):
        self.assertEqual([OK, OK, OK], run(OK, [WARN, OK, WARN]))

    def test_a_changed_proposal_restarts_the_count(self):
        # WARN then WATCH is not two agreeing samples.
        self.assertEqual([OK, OK, WATCH], run(OK, [WARN, WATCH, WATCH]))


class TestClearing(unittest.TestCase):
    def test_returning_to_ok_takes_five(self):
        self.assertEqual([WARN, WARN, WARN, WARN, OK], run(WARN, [OK] * 5))

    def test_four_good_samples_are_not_enough(self):
        self.assertEqual([WARN] * 4, run(WARN, [OK] * 4))

    def test_one_bad_sample_restarts_the_clear(self):
        seen = run(WARN, [OK, OK, OK, OK, WARN, OK, OK, OK, OK])
        self.assertEqual(WARN, seen[-1], 'the clear must start over')

    def test_worsening_is_still_fast_while_bad(self):
        # watch -> warn is an escalation even though we are already not ok.
        self.assertEqual([WATCH, WARN], run(WATCH, [WARN, WARN]))

    def test_improving_within_the_ladder_is_slow(self):
        self.assertEqual([WARN] * 4 + [WATCH], run(WARN, [WATCH] * 5))


class TestUnknownIsOutsideTheLadder(unittest.TestCase):
    def test_unknown_applies_at_once_without_counting(self):
        # It is not a severity - it means we could not judge this poll, and
        # pretending yesterday's verdict still holds would be a lie.
        self.assertEqual([UNKNOWN], run(OK, [UNKNOWN]))

    def test_leaving_unknown_applies_at_once_too(self):
        self.assertEqual([OK], run(UNKNOWN, [OK]))

    def test_unknown_does_not_poison_a_later_count(self):
        self.assertEqual([UNKNOWN, UNKNOWN, WARN], run(OK, [UNKNOWN, WARN, WARN]))


class TestConfigurableThresholds(unittest.TestCase):
    def test_up_and_down_are_parameters(self):
        self.assertEqual([OK, OK, WARN], run(OK, [WARN] * 3, up=3))

    def test_down_is_a_parameter_too(self):
        """P1 triage F-a: `down` was covered by NO test despite the name above.

        Replacing `else down` in apply_hysteresis with a literal 5 left the
        whole suite green, so the five-cycle clear - the asymmetry that is the
        entire point of this module, and the half the operator watched by hand
        during the P1 exit trial's step 7 - was pinned only by the constant
        happening to equal the default.
        """
        self.assertEqual([WARN, WARN, OK], run(WARN, [OK] * 3, down=3))

    def test_clearing_is_slower_than_escalating_by_default(self):
        # The asymmetry itself, stated as a property rather than as two
        # constants: an operator should learn quickly that something turned
        # bad, and green should not come back until it is convincingly fine.
        self.assertGreater(health.CLEAR_AFTER, health.ESCALATE_AFTER)
        escalate = run(OK, [WARN] * 6)
        clear = run(WARN, [OK] * 6)
        self.assertLess(escalate.index(WARN), clear.index(OK))


class TestNodeOverall(unittest.TestCase):
    def ind(self, **states):
        return {k: health.Indicator(v, None, '') for k, v in states.items()}

    def test_everything_healthy_is_ok(self):
        self.assertEqual('ok', health.node_overall(['ok', 'ok'], self.ind(capacity=OK)))

    def test_every_domain_unreadable_is_unknown(self):
        self.assertEqual('unknown', health.node_overall(['unknown', 'unknown'], {}))

    def test_an_unrecognised_status_is_not_read_as_healthy(self):
        """P1 triage F-b: this returned 'ok'.

        The PHP sibling um_rollup (api/nodes.php) normalises anything it does
        not recognise to 'unknown' and says so; this half let it fall through
        every branch to 'ok'. So the two implementations of one rule disagreed
        in the worst direction - a status neither of them understands read as
        HEALTHY on the Python side.
        """
        self.assertEqual('degraded', health.node_overall(['ok', 'stale'], {}))
        self.assertEqual('unknown', health.node_overall(['stale'], {}))

    def test_no_domains_at_all_is_unknown(self):
        self.assertEqual('unknown', health.node_overall([], {}))

    def test_one_blind_domain_among_readable_is_degraded(self):
        # The correction made live on 2026-08-26: one slow disks query must not
        # declare a nine-domain-healthy node unreachable.
        self.assertEqual('degraded',
                         health.node_overall(['ok', 'unknown'], self.ind(capacity=OK)))

    def test_a_domain_error_is_degraded(self):
        self.assertEqual('degraded', health.node_overall(['ok', 'error'], self.ind(capacity=OK)))

    def test_a_warn_indicator_degrades_a_reachable_node(self):
        self.assertEqual('degraded', health.node_overall(['ok'], self.ind(capacity=WARN)))

    def test_a_watch_indicator_also_degrades(self):
        self.assertEqual('degraded', health.node_overall(['ok'], self.ind(thermal=WATCH)))

    def test_an_unknown_indicator_never_makes_a_node_unknown(self):
        # Constraint 5. A box reporting no disk temperature is readable.
        self.assertEqual('ok', health.node_overall(['ok'], self.ind(thermal=UNKNOWN,
                                                                    capacity=OK)))

    def test_unknown_indicators_alone_do_not_degrade_either(self):
        self.assertEqual('ok', health.node_overall(['ok'], self.ind(thermal=UNKNOWN)))


class TestABlindPollIsNotAnObservation(unittest.TestCase):
    """P1 triage P2-2. A poll that could not judge is an ABSENCE of
    information. It must neither advance a count nor reset one.

    Entering unknown used to clear pending_state/pending_count, so a single
    blind poll laundered a warning away: two OK samples, one lost poll, one
    more OK and the card was green after four cycles instead of five. The
    operator watched that five-cycle clear by hand during the P1 exit trial;
    it is the asymmetry the whole module exists for.
    """

    def test_a_blind_poll_does_not_launder_a_pending_clear(self):
        seen = run(WARN, [OK, OK, UNKNOWN, OK, OK, OK])
        self.assertEqual([WARN, WARN, UNKNOWN, UNKNOWN, UNKNOWN, OK], seen)

    def test_the_clear_still_takes_five_good_samples_in_total(self):
        # Stated as a count rather than a sequence, so a fix that merely
        # shifted the blind poll one place along would not satisfy it.
        seen = run(WARN, [OK, OK, UNKNOWN, OK, OK])
        self.assertEqual(UNKNOWN, seen[-1],
                         'four good samples plus a blind one is not five')

    def test_a_blind_poll_does_not_reset_a_pending_escalation_either(self):
        # The same rule pointing the other way: one WARN, a lost poll, one
        # more WARN is still two agreeing observations of a warning.
        self.assertEqual([OK, UNKNOWN, WARN], run(OK, [WARN, UNKNOWN, WARN]))

    def test_good_news_after_a_quiet_spell_is_still_believed_at_once(self):
        # The rule this must NOT break: nothing was pending, so the node
        # answering and being fine is believed immediately.
        self.assertEqual([UNKNOWN, OK], run(OK, [UNKNOWN, OK]))

    def test_a_long_blind_spell_holds_the_count_it_had(self):
        seen = run(WARN, [OK, OK, UNKNOWN, UNKNOWN, UNKNOWN, OK, OK, OK])
        self.assertEqual([UNKNOWN] * 5, seen[2:7],
                         'three blind polls neither advance nor reset the two')
        self.assertEqual(OK, seen[-1], 'the third good sample after is the fifth')


if __name__ == '__main__':
    unittest.main()
