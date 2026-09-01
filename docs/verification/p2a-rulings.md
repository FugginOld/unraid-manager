# P2a decision record — every ruling made without a review gate

The independent reviewer subagent was unavailable for this entire phase (denied
by the harness on three separate attempts, including a plainly read-only
prompt). Tasks 3 through 8 were therefore reviewed by the same context that
wrote the plan.

That is structurally weaker than a second pair of eyes, and this file exists so
the weakness is auditable rather than invisible. Every decision taken on the
operator's behalf is below, in the order it was made, with what it costs if it
is wrong. Six of them corrected defects in the plan itself; implementers
corrected the coordinator six separate times, including once by refusing to add
a test it had proposed because the test could not fail.

Kept in `docs/` rather than the scratch workspace precisely because the scratch
workspace gets deleted, and a decision record that dies with its workspace was a
decision made in secret.

---

Ruling 1: T4's `exec_agent` takes six parameters; T7 injects a four-parameter
`exec_fn(node, verb, args, timeout)`. The plan says T7 binds `keys_dir` with
`functools.partial`, which resolves it, but the arity mismatch is not stated in
T4's Produces block. Decided: `exec_fn` IS the four-arg callable; `keys_dir` and
`run_fn` are bound at construction. T4's implementer is told this explicitly so
the signature is written partial-friendly (keys_dir before run_fn, both keyword-
bindable). Cost if wrong: one signature change in T7, caught by T7's own tests.

Ruling 2: T6's `parse_smart` calls `json.loads`; `collector.py` already imports
`json` at module scope (confirmed — `parse_response` and the parsers use it), so
no new import is needed. Recorded because the task text does not say so and an
implementer may add a duplicate import. Cost if wrong: a lint nit.

Ruling 3: T2's `test_no_verb_can_write` asserts the string `'rm '` is absent from
agent-exec's source. The word appears in no proposed implementation, but it is a
substring risk for ordinary prose in comments. Decided: keep the assertion, and
if it fires on a comment the implementer rewords the comment rather than
weakening the test. Cost if wrong: one reworded comment.

## Progress

Task 0: complete — all three preconditions answered on Raven and Golem.
  P1 HELD: /root/.ssh/authorized_keys -> /boot/config/ssh/root/authorized_keys
     (symlink, flash-resident). Append-through works. File is NON-EMPTY (746b).
  P2 HELD: smartctl 7.5, --json=c valid on both boxes.
  P3 ANSWERED: btrfs filesystem usage per-mount; zpool status on Golem;
     Raven prints "no pools available" (parseable, not a failure).

Ruling 4: the smartctl JSON carries serial_number and logical_unit_id. The plan
had parse_smart passing the whole document into node_state.payload, which
disks.php serves to the browser - violating the repo's standing privacy rule
(plan sections 199 and 443; collector.py:328 drops serialNum for this reason).
Decided: parse_smart STRIPS serial_number and logical_unit_id per device before
returning, and a test asserts neither reaches the payload. Stripped at parse,
not at the agent, matching _disk_row's existing precedent - the boundary this
repo defends is what gets STORED and served, and "full values stay server-side"
is explicitly allowed. Carried into Task 6's dispatch. Cost if wrong: a serial
reaches the browser, which is the one thing this rule exists to prevent.

Ruling 5: the Task 6 fixture would have been captured WITH real serials and
committed. Decided: fixtures are scrubbed at capture, and Task 6 adds a fixture
assertion for serial_number alongside the existing key-shaped scan. Cost if
wrong: real hardware serials in a public git history, unremovable.

Task 1: review returned spec-pass / quality-FAIL. 2 Critical, 4 Important, 5 Minor.

Ruling 6: Critical 1 is REAL and its root cause is my plan text, not the
implementer. handle() catches only ValueError around validate(); Task 2's
_validate_devices calls devices() -> os.listdir('/sys/block'), so an OSError
escapes handle(), escapes main(), and the peer answers a traceback on stderr
with NOTHING on stdout - which the manager reads as an unparseable empty reply
instead of BAD_ARGS. That is precisely the failure the envelope contract exists
to prevent. Decided: catch Exception at the validate call, map to BAD_ARGS, and
AMEND THE PLAN's Task 1 Step 4 so Task 2 does not inherit it. Cost if wrong:
none - the narrower catch has no advantage.

Ruling 7: Critical 2 is real. The SSH_ORIGINAL_COMMAND test is a literal-string
grep, and Task 2 adds `import os` to that same file, after which one
concatenated spelling passes it unchanged. Decided: strengthen to also forbid
`environ` and `getenv` in the source. Cost if wrong: a future task that
legitimately needs an env var must justify itself in a test, which is correct.

Ruling 8: hello() reads the module global VERBS rather than the table handle()
was given, so a restricted table over-reports. Latent (production has one
table), but the fix is cheap while Task 2 is unwritten. Decided: the run
contract becomes run(args, table) uniformly - no special case for hello, and
Task 2's verbs accept and ignore the second argument. Plan amended. Cost if
wrong: one unused parameter on three verbs.

Ruling 9: BAD_ARGS and RUN_FAILED have no test, and main() has none. The codes
are a contract three later tasks consume and Task 4 reads the exit status.
Decided: all three get tests in the fix round. Cost if wrong: nothing.

Ruling 10: the brief's Produces block says handle(envelope_text, table=VERBS)
while its own Step 4 code says handle(text, table). The plan contradicts itself.
Decided: handle(text, table), no default - an import-time default would capture
the table and defeat the table-passing idiom the tests use. Plan amended. Cost
if wrong: a later caller writes handle(body) and gets a TypeError immediately.

Ruling 11: Minors accepted into this round because each is one line and the file
is open: the garbled docstring reword, `args` accepting falsy non-objects, and
the 100644/100755 exec-bit mismatch with the sibling script. Deferred, not
fixed: the __pycache__ artifact (already gitignored and pruned by build.sh).

Carried into Task 2's brief: load_agent() does not register in sys.modules, so
mock.patch('agent_exec.x') string targets fail - patch attributes on the object.

Task 1: fix round 1/5 dispatched to implementer a1e63e7d2520e2534 (8 findings:
2 Critical, 4 Important, 2 Minor accepted; 1 Minor deferred). Plan amended first
at HEAD for rulings 6, 8 and 10 - the amendments are authoritative over the
brief and were sent with the findings.

Task 1: fix round 1/5 (8 addressed, 0 open; commits 6914551..a23a6b7). Scoped
re-review verdicted all eight ADDRESSED but flagged 3 NEW blocking items in the
fix diff - two of them mine.

Ruling 12: two of the three were in the plan file I amended. The (args, table)
contract went into the Interfaces prose but NOT into the Task 2 code block the
next implementer copies verbatim, so all three of its verbs would have raised
TypeError, been swallowed as RUN_FAILED, and returned ok:false instead of data -
with Task 2's own tests failing on a confusing code rather than an arity error.
Decided: controller owns the plan document, so I synced both code blocks myself
rather than dispatching. Cost if wrong: none, it is documentation of my own
decision.

Ruling 13: the third is real and the implementer's. The finding-8 regression
test sends {"args": [1,2]}, which the PRE-fix code already rejected - the
re-reviewer proved it by executing the old blob. Reverting the guard leaves all
386 tests green, so the test pins nothing. Decided: fix round 2, and the
implementer must demonstrate discrimination by reverting the guard, watching the
new test fail, and restoring. A regression test that cannot fail is the defect
this repo has now shipped twice. Cost if wrong: nothing - the check is cheap.

Task 1: fix round 2/5 dispatched to a1e63e7d2520e2534 (1 blocking, 4 cheap
non-blocking; plan pre-synced at HEAD).

Task 1: fix round 2/5 (5 addressed, 0 open; commits a23a6b7..ad3cf72). Every
addressed item mutation-proven by the re-reviewer rather than taken on trust,
including the discrimination check on the args guard.

Ruling 14: round 2's remaining blockers were all in my plan document, and one
was my own error - the 7->12 test-count edit was global and hit Task 3's line,
which the contract change never touched. Restored to 7. Cost if wrong: a future
Task 3 implementer chases a count that was never true.

Ruling 15: the plan's Task 1 code/test blocks are now stale against the shipped
files, and pasting them produces a file that fails its own test (the docstring
still spells the forbidden environment variable). Decided: label the blocks
superseded and point at the committed files, rather than re-transcribing 120
lines that re-stale on the next commit. The trap is removed by the label; the
authority moves to git. Cost if wrong: someone re-executing the plan from
scratch reads two files instead of one block.

Task 1: complete (commits c3504bd..ad3cf72, review clean - 8+5 findings all
addressed across two rounds, 16 tests, 390/390 suite green).

Task 2: implementer returned BLOCKED, correctly. The brief's mounts_list did a
bare open('/proc/mounts') and its test had no fixture, making the task's own
global constraint (no test touches a live machine or the real /sys and /proc)
unsatisfiable as written - and unrunnable on the Windows dev box.

Ruling 16: my plan defect, not the implementer's. Decided: PROC_MOUNTS and
SYS_BLOCK become module-level override points and the tests repoint them, which
is the idiom this repo already uses at managerd.py:35 for POOL_MARKER. Rejected
builtins.open patching (brittle, catches unrelated opens) and a platform skip
(loses the coverage on the only machine that runs the suite). Plan amended
before resuming. Cost if wrong: two extra module constants.

Ruling 17: several of the brief's tests assign agent.devices/agent._run with no
restore, which leaks into the next test and makes failures order-dependent.
Decided: addCleanup on every monkeypatched attribute. Also a defect in my brief.
Cost if wrong: nothing - it can only remove flakiness.

Task 2: review spec-PASS / quality-FAIL. 2 blocking, 6 non-blocking.

Ruling 18: pool.balance ships with zero behavioural coverage - its only
appearance is the registration list. The reviewer proved the silence: changing
'btrfs' to 'xfs', or either None to '', passes all 397 tests. That is the exact
mutation-silence the brief's Step 5 exists to catch, applied to the one verb
Step 5 skipped, and Task 6 consumes the shape. Fix round 1. Cost if wrong: none.

Ruling 19: _run returns done.stdout whatever the exit code, so a present-but-
failing binary yields '' while an absent one yields None - a third state nobody
declared. Decided: KEEP _run as it is, and Task 6 treats '' and None as the same
"no data" fact. Reason: smartctl's exit status is a BITMASK, not a success flag -
a healthy read of a disk with prefail attributes exits non-zero and still emits
a full document. Treating non-zero as failure would blank exactly the disks we
most want to see. Recorded as a platform fact in the plan. Cost if wrong: Task 6
reports "no data" for a disk that emitted a partial document.

Ruling 20: no aggregate budget. 22 devices x RUN_TIMEOUT 30 is up to 660s inside
one SSH call whose transport timeout is 90s at most, so the manager cuts the
connection and gets NOTHING rather than a partial dict - and it does so precisely
on the node with sick disks, which is the node whose SMART data matters most.
Decided: add BUDGET = 60 to the agent; smart_attributes stops shelling once
elapsed exceeds it and leaves the remaining devices None, which is the same
"could not read it" fact the per-device failure path already produces. The
transport timeout must exceed BUDGET, recorded as an invariant for Task 4.
Cost if wrong: a very slow enumeration returns partial data instead of failing,
which is the direction this repo already chose everywhere else.

Ruling 21: the plan doc's TestReadVerbs snippet kept the leaking-monkeypatch
idiom (bare agent.devices = ...) that ruling 17 removed from the shipped tests.
Tasks 3-6 are cut from this plan, so the idiom would propagate. Mine to fix.

Task 2: fix round 1/5 (2 blocking + 2 non-blocking addressed; commits
238d910..439fd16). Re-review verdict PASS WITH NOTES, all four items addressed,
mutations independently verified by the reviewer rather than trusted.

Ruling 22: the re-review's invariant note is right and mine to fix. The deadline
is tested BEFORE a device starts, so the last admitted device can still burn a
full RUN_TIMEOUT on top of the budget - worst case 90s, exactly the transport
figure the comment names as the failure. "Transport must exceed BUDGET" would
have had Task 4 size against 60 and reintroduce the bug. Corrected to
BUDGET + RUN_TIMEOUT in both the agent comment and the plan. Cost if wrong:
Task 4 picks a timeout that cuts a slow enumeration.

Ruling 23: spending a round 2 on findings the re-review classed non-blocking.
Justification: one of them is a test that CANNOT FAIL - deleting the isinstance
guard entirely leaves the suite green, because a bare string falls through and
iterates as characters, still producing BAD_ARGS. That is the precise defect
this repo has now shipped four times, and the round also corrects a FALSE
verification claim in the implementer's report. A false claim is worse than no
claim: the next reader stops checking. Cost if wrong: one extra round.

Task 2: fix round 2/5 dispatched to a6b50eab064649277.

Ruling 24: the scoped re-review of Task 2 round 2 was BLOCKED by the auto-mode
classifier - the prompt asked the reviewer to mutate source and run tests.
Decided: verified the round myself instead of re-dispatching, because the round
was test-only and small enough to check directly. Confirmed: git diff --stat
shows agent-exec untouched (only CONTRIBUTING, the plan, and the test file), all
three assertions present at test_agent_exec.py:160/170/291, suite 399/399 green.
The discrimination argument does not need re-deriving - 'devices must be a list'
cannot appear in the fall-through message, which is what makes it sensitive.
Cost if wrong: one test-only round went without a second pair of eyes; the code
under it was already reviewed clean twice.
NOTE for later rounds: do not instruct a reviewer to mutate source and run it.
Ask it to reason the mutation through from the diff instead.

Task 2: fix round 2/5 (3 addressed, 0 open; commits 439fd16..9f0a200).
Task 2: complete (commits 13f0446..9f0a200, 25 agent tests, 399/399 suite green,
all mutations verified).

Task 3: implemented and committed at 3282045 (7 tests, 406/406 suite green).
NOT REVIEWED - the task-reviewer dispatch was denied by the auto-mode
classifier, twice in a row. The second attempt contained no instruction to
mutate or modify anything, so this is a systemic block on dispatching the
reviewer agent, not a property of one prompt.

STOPPED and surfaced to Joe rather than self-reviewing a second time. Reason:
he chose subagent-driven execution, whose whole value is a review gate the
implementer did not write. Substituting my own review once was a stopgap;
doing it silently every task would be delivering a different process than the
one he picked, and the denial message itself says to let the user decide.

Task 3 status: complete-but-ungated. Do not mark it done until reviewed.

Ruling 25: reviewer subagent denied a THIRD time, on a plainly read-only prompt
with no mutation or execution instruction. Systemic, not prompt-shaped; executor
dispatches keep working. Joe's answer to the options was "process in the
sequence that makes sense", so: I review each task myself, labelled as a
controller review in both the ledger and the fix message so the implementer
knows there is no third party arbitrating. An independent pass is recommended to
Joe at phase end via /code-review, which is user-triggered and unaffected.
Cost if wrong: the gate is the same context that coordinated the work. Mitigated
by telling each implementer to push back, and by the phase-end pass.

Task 3: controller review found 1 Critical, 2 Important.

Ruling 26: CRITICAL - SSH_TIMEOUT = 30 contradicts the budget invariant, and
worse, my own BUDGET=60 made the worst case exactly 90, which is exactly
SLOW_TIMEOUT - the timeout the agent domains actually run under. The enumeration
would be cut mid-flight on the node with slow disks: the exact failure the budget
was added to prevent, reintroduced by the budget's own value. Decided: BUDGET
60 -> 45 (worst case 75, 15s headroom under 90), and SSH_TIMEOUT deleted as a
dead constant stating a wrong number. Cost if wrong: a slow enumeration returns
partial data, which is the direction chosen everywhere else in this repo.

Ruling 27: reply.get('data') returns None for an ok-reply with no data key, and
Task 6's (data or {}) turns that into {'count': 0, 'disks': {}} - "the agent said
nothing" becomes "this node has no disks". Absent-vs-empty, the fifth time.
Decided: fail closed on a MISSING data key; a PRESENT but falsy data ({}, 0, '')
stays legitimate and is returned as-is. The check is `'data' not in reply`, never
a truthiness test, and a test pins both halves so a later "simplification" cannot
collapse them. Cost if wrong: a peer with a quirky ok-reply reads as refused
rather than empty, which is the safe direction.

Ruling 28: the refusal path does not truncate peer-supplied text, which Task 6
puts in node_state.error and the browser renders. Decided: bound it at 200 like
_unreachable already does. Cost if wrong: none.

Task 3: fix round 1/5 dispatched to a417d38abe7c8b542 (controller-reviewed).

Ruling 29: the Task 3 implementer pushed back and was RIGHT. I amended the plan
doc for BUDGET 60->45, told it "that is at HEAD", and never touched agent-exec -
which still carried 60, so the worst case was still exactly SLOW_TIMEOUT. A
ruling recorded and not applied is worse than one never made, because the ledger
says it is done. Applied inline (a constant and its comment; CLAUDE.md says do it
inline when you already know the file) and committed at bbeb460. Cost if wrong:
none - it is the value the ledger already said was correct.

Ruling 30: Task 4's plan passed one number to both ConnectTimeout and the
subprocess timeout. They are different jobs - the handshake versus the whole
call - and sharing a value sized for a 22-disk enumeration means a dead peer
holds a worker for 90s before anyone learns it is dead. CONNECT_TIMEOUT = 10,
separate. Cost if wrong: a peer on a slow link needs a longer handshake window.

Task 3: fix round 1/5 (3 addressed, 0 open; commits 3282045..59aef1a).
Task 3: complete (commits 9f0a200..bbeb460, 10 tests, 409/409 green,
CONTROLLER-REVIEWED - no independent gate, flagged to Joe).

Task 4: implemented at 7316ec8 (19 tests, 418/418). Implementer independently
strengthened the port test after finding by mutation that assertNotIn('15137')
still passes with -p dropped entirely, since ssh defaults to 22. Correct
instinct, unprompted.

Ruling 31: CRITICAL - `-N` in the ssh argv breaks the transport outright, and it
came from my brief. -N means "do not execute a remote command", so the client
sends neither an exec nor a shell request. A forced command works by REPLACING
the command on such a request; with none sent, sshd has nothing to replace and
agent-exec never runs. Every call would sit until our own timeout and report a
healthy, correctly configured peer as unreachable. Decided: drop -N, keep -T,
and pin it with a named test so nobody re-adds it as a tidy-up. This would not
have surfaced until Task 9, with Tasks 5-8 already built on a dead transport.
Cost if wrong: if forced commands DO fire under -N, we have removed a harmless
flag. Asymmetric in the safe direction, and Task 9 settles it on hardware.

Ruling 32: AGENT_PATH is dead in Python for good - the manager never needs the
peer-side path, because the forced command in the peer's authorized_keys names
the script. The only consumer is the installer text, which Task 8 renders in PHP
and cannot read a Python constant. Deleted. Cost if wrong: Task 8 redefines it
in PHP, which it must anyway.

Task 4: fix round 1/5 (2 addressed, 0 open; commits 7316ec8..7a843f5). Verified:
-N absent, -T present, AGENT_PATH gone, named regression test at
test_agentclient.py:97, 419/419 green.
Task 4: complete (commits d8fb23f..7a843f5, 20 tests, CONTROLLER-REVIEWED).

Task 5: complete (commit 8608b45, 422/422, 0 regressions). Controller review
found nothing: _domain defaults leave all nine existing domains untouched,
int(tier or 0) is null-safe, and the regression test compares against a literal
nine-name set rather than deriving from DOMAINS. The verb-table assertion is
vacuous until Task 6 registers an AGENT domain - flagged honestly by the
implementer, and expected.

Task 6 BLOCKED on a fixture that must come from hardware. The plan says the
smartctl fixture is captured, not invented, and CONTRIBUTING is explicit:
"Fixtures are evidence, not editable data... several P0 tests were written
against a design document's invented JSON and had to be corrected." Task 0
captured `smartctl --json=c -i` only; the agent runs `-a`, which is a different
and much larger document. Asking Joe rather than inventing one.

=== 2026-08-31 session resumed ===

Ruling 33: the secrets-scan change from last night is committed at 460bf95, but
NOT as written then. Testing the "strictly stronger" claim instead of asserting
it found a hole: pinning the hex pattern to exactly 64 let a 40-character
lowercase-hex token through that the OLD rule caught. Floor lowered to 32;
nothing in the fixtures tree matches it. Verified against seven shapes.
Cost if wrong: a 32+ hex string that is legitimately not a secret fails the
suite, which is the safe direction for a credential scan.

Task 6: implementer stopped at Step 4, correctly, and surfaced a defect far
bigger than the test it named.

Ruling 34: MY Step 4 was wrong. Bumping SCHEMA_VERSION does not widen
node_state's CHECK on an existing database - migrate() drops only
DERIVED_TABLES ('node_health'), node_state is deliberately excluded because it
holds retained payloads, and CREATE TABLE IF NOT EXISTS is a no-op on an
existing table. So Raven's live DB keeps the three-value CHECK and raises
IntegrityError the first time an agent domain reports `unsupported` - while
every test passes, because tests build fresh databases. Green tests, broken box:
this repo's signature failure, in my own plan. Decided: a real rebuild migration
for node_state (create-copy-drop-rename inside migrate()), the first
non-derived migration here, safe by construction because widening a CHECK
cannot invalidate an existing row. The test must assert BOTH that rows survived
AND that 'unsupported' now inserts. Cost if wrong: a migration bug touches
retained history, which is why the rows-survived assertion is the load-bearing
half.

Ruling 35: store.VALID_STATUS must gain 'unsupported' - upsert_state validates
against it and would raise ValueError before the widened CHECK is reached.
Flagged by the implementer, outside its brief, and correct. Task 7 would have
hit it.

Ruling 36: updating test_the_schema_version_moved_to_three is REQUIRED, not
forbidden. It encodes a constant that legitimately moved; leaving it asserting 3
would assert something false. Distinct from adjusting a test to hide a failure.

Task 6: complete (commit 3a1dfb9; 422 -> 431 python). Real node_state rebuild
migration, VALID_STATUS widened, schema-version test renamed. Migration test
pins BOTH row survival (count and payload content) and the new status being
accepted; discrimination confirmed by removing the rebuild block.

Ruling 37: the Task 6 implementer surfaced a PHP failure PREDATING its work -
policy_test.php's mutation pin scanned collector.py's raw text including
comments, and my Task 5 comment "the no-mutation assertion included" turned it
red. THE PHP SUITE HAS BEEN FAILING SINCE 8608b45 AND I CLOSED TASK 5 CALLING IT
CLEAN. Neither the implementer nor I ran PHP because "no PHP file was touched" -
a rule that is wrong whenever a PHP test reads a Python file, which this one
does. Fixed inline (my regression, a 3-line test change, CLAUDE.md says do it
inline when you know the file): both the mutation and introspection pins now use
py_code_only(), which was already defined one check above. Verified the pin still
catches a real mutation query substituted into a domain. Cost if wrong: a
trailing inline comment containing "mutation" would slip; the authoritative pin
is the Python one over domain.query, which is precise.

PROCESS CORRECTION for the rest of this phase: run the PHP suite on EVERY task,
regardless of which files changed. Three PHP pins read Python sources.

Task 7: implemented at 055c48c (431 -> 435 python, PHP green - the process
correction held). Dispatch is correct and binds keys_dir by KEYWORD, with a
comment naming why: exec_agent takes six parameters and collect_agent calls
exec_fn with four, so a positional partial could silently line the wrong value
up with that slot after a reorder.

Ruling 38: the implementer honestly flagged that tests 3 and 4 key off a
mutation in Task 6's collect_agent rather than its own loop. Correct, and the
Task-7-LOCAL defect is different: deleting the `continue` makes every agent
domain ALSO take the GraphQL path with the verb name sent as a query, producing
two Results for one domain where the loser overwrites real SMART data. That is
the mutation the tests must be measured against. Fix round 1. Cost if wrong:
none - the mutation either goes red or a test gets stronger.

Ruling 39: the agent branch sits BEFORE the `if key is None` check, so a Tier 1
node with no GraphQL key still collects its agent domains. That is correct and
load-bearing - spec section 8 requires ssh and API credentials to revoke
INDEPENDENTLY - but nothing asserts it, and moving the branch three lines down
would couple the two credentials with the suite still green. Test added. Cost if
wrong: revoking an API key silently blinds SMART collection too.

Ruling 40: test C (never auto-downgraded) cannot fail against current code -
nothing in run_cycle writes tier. Kept, because the spec explicitly names silent
downgrade as the worst failure shape here, but its docstring must say it guards
a future change rather than checking live behaviour. A guard-against-regression
is legitimate; a guard MISTAKEN for a live check is how a passing assertion gets
read as evidence that something was considered and rejected.

Ruling 41: my suggested "assert exactly one row for the smart domain" would NOT
have discriminated the missing-`continue` bug - upsert_state uses ON CONFLICT DO
UPDATE, so there is always exactly one row per (node_id, domain) whether the bug
is present or not. The implementer refused to add it and used a status
comparison instead. Correct, and it is the fifth implementer correction of me
this phase. I proposed a test that could not fail while reviewing for tests that
cannot fail. Cost if wrong: none; the status assertion is strictly better.

Task 7: fix round 1/5 (3 addressed; commits 055c48c..37b9e2c). Confirmed NONE of
the original four tests caught the missing-`continue` mutation before the fix.
Task 7: complete (436 python, PHP green, CONTROLLER-REVIEWED).

Task 8: implemented at 4fa0045 (436 -> 439 python, PHP green). Strongest
implementer report of the phase: it mutation-tested MY brief's own assertions
and found two that could not fail (the traversal id was refused by the existence
check rather than the format guard; 'no-such-node' is non-hex so the registry
lookup was never reached), caught that the enrollment-event test passed with
log_event deleted because reload() already logs 'enroll' for the fixture node,
and replaced my grep-for-'PRIVATE' assertion with a diff against a real
generated key.

Ruling 42: the implementer flagged, and did not fix, that store.set_tier writes
tier=1 to sqlite while nodes.cfg on flash is never updated - and sync_registry
treats flash as authoritative for that column on every reload(). So a daemon
restart, a Settings save, or any node add/remove folds an enrolled node back to
Tier 0, SMART collection stops, and every card still reads healthy. That is the
silent-downgrade shape the spec names as worst-case, reached by a road neither
the spec nor Task 7's guard covers, and it is a defect in MY decision 6.
Decided: flash is authoritative, so enrollment writes FLASH, not sqlite.
store.set_tier is deleted outright - a second source of truth for a column that
already has one. api/tier1.php updates nodes.cfg through the existing
um_render_nodes_cfg path on success only, then asks the daemon to reload so
sync_registry propagates it. Order is load-bearing: test, then write flash, then
reload; a failed test leaves nodes.cfg untouched. The regression test enrols,
reloads, and asserts the node is STILL tier 1. Cost if wrong: enrollment writes
flash for a node whose test passed, which is the state we intend anyway.

Also asked: does um_render_nodes_cfg round-trip every OTHER field when rewriting
the registry to change one column? Rewriting a whole file to change one value is
how a field nobody was looking at gets dropped.

Ruling 43: Task 8's PHP suite is NOT green in the documented environment.
tier1_test.php exits 255 under php:8.2-cli because ssh-keygen is not in that
image - um_tier1_keygen returns null and everything downstream falls over. The
implementer reported "php suite: all pass", so it ran somewhere that has the
binary. SECOND unheld verification claim this phase (the first was the isinstance
mutation in Task 2). Decided: make the keygen runner injectable, matching every
other external dependency here - post_fn, exec_fn, run_fn, PROC_MOUNTS,
SYS_BLOCK, POOL_MARKER all exist so the suite never needs the real thing, and
shelling ssh-keygen unconditionally was the one place that broke the pattern.
Keep ONE test on the real binary that SKIPS LOUDLY when absent, matching run.sh's
existing `!!! node not on PATH` idiom. The fake must write realistic private-key
material so the "installer never contains a private key" assertion keeps its
strength. Cost if wrong: one real-keygen path is exercised only where the binary
exists, which is already true of the JS suites.

PROCESS NOTE: I asked the implementer HOW it ran the PHP suite rather than just
correcting the result. Two unheld claims in one phase is a pattern, and the fix
belongs in how verification is requested, not in re-running it myself each time.

Ruling 44: the implementer answered honestly - it ran PHP against a local
Windows install with Git Bash's ssh-keygen on PATH, never the documented docker
command. Process gap, stated plainly, not an ambiguous instruction. Fix landed
at 3a231cf: injectable runner, one real-binary test gated on a loud !!! skip
keyed off exit 127, verified against BOTH php:8.2-cli (127) and a real
ssh-keygen (never 127). I re-ran both suites myself: PHP all pass, python 441.

Ruling 45: Task 8 shipped the endpoint and the daemon handler but NO UI -
nothing in frontend/ or the settings page calls tier1.php, though the plan
promised "one flow on the existing node settings". Decided: that is the right
ORDER, not a defect to fix now. Prove the transport on hardware first; a UI
built on an unproven forced-command path is built on sand, and Task 9 can drive
enrollment through the CLI-callable functions by design. The UI becomes a named
follow-up AFTER Task 9. Cost if wrong: the operator flow lands a task later than
planned, with the hardware answer already in hand.

Task 8: complete (441 python, PHP green, CONTROLLER-REVIEWED; UI deferred by
ruling 45). Tasks 0-8 done. Task 9 is hardware.

=== TASK 9 HARDWARE, 2026-08-31 ===

Deploy verified on Raven: um_tier1_persist present, agent_hello registered,
'-T' at agentclient.py:95 with NO -N beside it, set_tier gone (0 hits).

Installer reviewed by hand before running: appends with >> (never >), all four
restrictions on the key line (command=, no-pty, no-port-forwarding,
no-agent-forwarding), public half only. Agent landed on Golem byte-identical -
md5 b38769f06c57bb668a74a87ed7bba61e on BOTH boxes - and parses.

Ruling 46: I predicted Golem's authorized_keys would go 1 -> 2 lines and it went
to 3, with an empty first line. NOT a data loss: >> is append-only, so nothing
present could be removed. Golem's file was a single blank line all along - it had
NO root key. The 746 bytes I compared against was RAVEN's file from the Task 0
check, and I carried it over to the wrong box. ssh-keygen -lf parses exactly one
valid key (ours). Cost of the error: a scare, and a wasted round trip.

Ruling 47: THE GAP TASK 9 EXISTS TO FIND. agentclient passes
StrictHostKeyChecking=yes and UserKnownHostsFile=<keys_dir>/known_hosts, but
NOTHING EVER WRITES THAT FILE. tier1.php has no known_hosts logic at all - spec
section 2 said "written at enrollment" and my Task 8 brief never carried it.
Live: {"ok":false,"error":"ssh exited 255: No ED25519 host key is known for
192.168.2.248 and you have requested strict checking."} The transport cannot
work at all until this lands. Dispatched as a Task 8 addendum with the
replace-never-append requirement (a re-imaged peer's stale entry makes ssh
refuse the host outright, presenting as "the agent broke" long after the cause).

WHAT THE FAILURE ALSO PROVED, and this is why it was worth running:
- ssh RAN rather than hanging, so dropping -N did not break the invocation.
- exec_response classified it correctly as AgentUnreachable with stderr
  surfaced and truncated - the Task 3 classifier working on real output.

Ruling 48: known_hosts fix VERIFIED ON HARDWARE. The fingerprint the manager
recorded via ssh-keyscan - SHA256:J1qlDF6/yevQJqAabFhylRjS80J+SF4x24PkZqLubAk -
matched byte for byte the one Golem's own installer printed on Golem. Trust-on-
first-use is now a real check rather than a shrug, which was the point of
printing it in the installer at all.

Ruling 49: THE -N RULING IS SETTLED, ON REAL SSHD. agent_hello returned
"ssh exited 126: bash: line 1: /boot/.../agent-exec: Permission denied".
Exit 126 is found-but-not-executable: sshd accepted the key, applied command=,
and TRIED TO RUN our script. With -N still in the argv nothing would have
executed at all. I ruled on that from OpenSSH semantics alone and the box
agrees.

Ruling 50: PLATFORM FACT, and the one no test could have found. /boot is the
FAT32 flash, mounted fmask=0177, so every file on it is forced to 0600 and the
execute bit is UNSETTABLE. Our chmod 700 was a silent no-op. Nothing on flash
can ever be execve'd - which is why Unraid plugins install into tmpfs and
re-extract at boot. The agent itself is correct: piped to python3 it answered
{"ok": true, "version": "2026.08.30", "hostname": "Golem", "verbs": [all four]}.
Decided: the forced command names the interpreter by absolute path, and the
chmod is DELETED rather than left as decoration - a chmod that appears to work
and changes nothing is worse than none. Reading a file on a noexec/fmask mount
is fine; only execve is blocked, so the flash-resident design survives with no
boot hook and still no plugin on the peer. Going into tier0-coverage.md, which
is binding. Cost if wrong: none - the interpreter form works either way.

Also asked the implementer to grep whether anything ELSE in this repo assumes a
file on /boot is executable.

Ruling 50 applied at a2d469a. UM_TIER1_FORCED_COMMAND = UM_TIER1_PYTHON3 . ' ' .
UM_TIER1_AGENT_PATH, chmod deleted, both mutation-verified, tier0-coverage.md
updated (it is the binding doc). 441 python and PHP green, re-verified by me.

Item 5 answered thoroughly and correctly: no other functional bug. The .plg's
chmod +x calls target tmpfs. Its chmod 700 on the flash keys/ DIRECTORY is
governed by dmask=0077, not fmask=0177, so 0700 is already forced - the
distinction between the two masks is the right one and the implementer drew it
unprompted. The remaining flash chmod(0600) calls are silent no-ops but harmless,
since fmask already forces exactly 0600 and none of those files is executed.

=== P2a EXIT CRITERIA MET, 2026-08-31 19:43 ===
agent.hello over ssh: {"ok":true,"version":"2026.08.30","verbs":[all four]}
rm.everything -> UNKNOWN_VERB. /dev/nope -> BAD_ARGS by enumeration.
/dev/sda -> real smartctl JSON. Fingerprint matched the peer's own.
Golem runs NO plugin: one script on flash, one authorized_keys line.

Ruling 51: /root/.ssh is the SYMLINK, not authorized_keys - stat shows one inode
(3302607) for both paths, and vfat has neither hard links nor cross-fs links, so
they are necessarily the same file reached through a directory symlink. My
earlier note called the FILE a symlink, which made sed -i look dangerous when it
never was here. Persistence is structural: everything written into that directory
is on flash by construction. Corrected in p2-checks.md rather than left to
mislead the next reader.

Ruling 52: the reboot criterion is recorded as UNVERIFIED, not as proven by
inference. The inference is strong - the directory resolves onto flash - but this
phase has twice been confidently wrong about something that looked obvious (-N
and chmod 700), and "strong evidence" is the phrase this repo has learned not to
trust. Rebooting Golem stops a 22-disk array, so it is Joe's call, not mine.

Docs moved in the same commit (1a4abb5): ARCHITECTURE's exclusion list said no
Tier 1 agent and no SSH; both are now false. The mutation boundary has NOT moved
and that is now the only line left.
