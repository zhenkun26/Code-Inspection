# Local walkthrough: review to handoff

This guide runs the existing four-stage contracts in a fresh, self-owned example. Use an existing Python 3.9+ on a supported POSIX host, or the bounded native Windows environment in [platform support](windows-support.md), and Git supporting `merge-tree --write-tree`. Local macOS checks and the documented CI matrix are distinct evidence; see [acceptance](acceptance.md). No package install, agent login or platform connection is involved. The Skill method layer still works through your existing agent host; see [the README](../README.md#start). On Windows, substitute `python` for `python3` if appropriate and let the example choose its temporary directory, or provide a new supported absolute local path.

## Run the complete example

From the checkout root, run:

```sh
python3 -B examples/lifecycle_demo.py --simulate-approvals --with-delivery
```

It prints a **new external output directory** and a JSON summary. Keep that directory: it contains the exact generated policy and SHA pin, initial facts, every CLI command/output, final ledger and handoff. Re-running creates another independent example. To select a location, append `--output /tmp/my-new-review-demo`; that exact directory must not already exist and must be outside the checkout. Existing output is refused, never overwritten or resumed. Do not use the script with a real host session, real approval records, or a production repository.

The explicit `--simulate-approvals` option permits fixture-only `test-human` records. It does not record a real person's approval. `--with-delivery` opts into simulated delivery; omit it to stop after the isolated merge/handoff. Without it, the after-merge stage is NOT_RUN/HOLD and no online acceptance is claimed. A demo summary of PASS means the illustrated transitions occurred as expected, including expected HOLD results. It is not a PR approval, security verdict or release qualification.

## What actually happens

| Step | Observable result | Evidence boundary |
| --- | --- | --- |
| Initialize facts and PR-before record | Reads exact old/new `note.txt` bytes from two fixture commits, writes a candidate workspace file, records its actual SHA-256 and a deterministic content check | Real tiny local check; no model, product journey or security scan. The pre-PR handoff is still a teaching example, not comprehensive review. |
| Import pre-PR handoff and begin PR review | Inherited budget is retained; missing checks and an incomplete security record produce HOLD/UNKNOWN | Review records are synthetic, labeled in the raw file. No hosted CI or independent reviewer is invoked. |
| Complete evidence; record simulated person | All checks may be PASS while the PR remains HOLD until the simulated decision is supplied | Demonstrates the separation of check results, stage recommendation and human authority; not authentication. |
| Prepare the combination | Records real bare-Git candidate/tree and source identity; preflight remains HOLD without integration evidence/authority | Real `merge-tree`/`commit-tree`; integration acceptance is a synthetic record. |
| Authorize and conditionally merge | `update-ref` checks the expected old base atomically; query reads the actual ref and confirmed receipt | Real self-owned Git objects/ref, no remote and no change to this checkout. |
| Optional deploy/enable/observe/promote | File provider changes; incomplete observation stays UNKNOWN/HOLD; a complete observation permits same-artifact promotion | All deployment, flags, samples, channel and approvals are local simulations. The 10 ms window is a toy fixture, not a proposed production threshold. |
| Export | `handoff.json` retains findings/evidence/events, current identities and cumulative budget; publication is NOT_RUN | Export does not authorize another action. |

The script calls [the existing lifecycle CLI](../interfaces/lifecycle/README.md); it does not implement a second gate policy. Exact statuses and stop rules remain in [the core contract](../skills/pre-pr-review/references/contract.md) and [budget/recheck protocol](../skills/pre-pr-review/references/recheck.md).

## Inspect and continue manually

In the printed output directory:

- `pre-pr-record.json` and `pre-pr-workspace/note.txt` show the small actual requirement check and snapshot.
- `fixture/host/policy.json`, `authority.json` and `state.json` show the policy, **simulated** decisions and continuous ledger. `fixture/inputs/` contains synthetic evidence; `fixture/bare.git/` is the self-owned merge target.
- `commands/NN-command.json` contains the actual argv, raw exit code, stdout and stderr for each CLI call. Copy a command only after confirming it points at this fixture. Do not copy a fixture approval helper into real use.
- `summary.json` includes source SHA-256 values and generated host/input/pin values. `handoff.json` is the final export.

Use the recorded `host_root`, `input_root` and `policy_sha` values with the interface guide's `pr-status`, `export`, `merge-query` or `delivery-query` commands. Those read commands do not begin a new round or repeat the action. Operation IDs come from actual plan/receipt output. Do not guess them or clear `state.json` to restart a blocked session. Interrupted/failed demos retain artifacts; start another fresh demo to retry the tutorial.

## Minimum configuration for a real review

Start by asking the host to read [the before-PR entry](../skills/pre-pr-review/SKILL.md). Supply the following filled task brief, then replace every example fact with the actual project facts. This is a **human-readable task brief**, not a second machine policy schema:

```text
Target: my explicitly selected checkout and relevant uncommitted files.
Original requirement: add the requested behavior; source is the user's task.
Acceptance: observable success plus the applicable error/boundary behavior.
Mode: read-only review; verification only in the environment/actions I authorize.
Policy: user/host-selected trusted rules, source, hash, scope and decision owner.
Risk/checks: justify actual impact, required checks, views and any applicability exception.
Budget: explicit task limits, inherited use, counting unit and stop/escalation owner.
Output: actual version, evidence, confirmed findings/questions, gaps and stage recommendation.
```

For optional B execution, the generated `fixture/host/policy.json` is a complete **valid minimal teaching policy** for this runner. It has the required CI/semantic/security PR checks and a combination check. Inspect it alongside [the authoritative field contract](../interfaces/lifecycle/README.md#最小配置与记录), then construct a separate host-owned policy for the real task. Do not treat fixture budget, reviewer count, check names, `low_auto`, humans, scopes or delivery thresholds as approved project defaults. The current merge backend accepts only the marked isolated bare repository; editing a path is not production integration.

| Field group | Trusted source and responsibility |
| --- | --- |
| task ID, original requirements/acceptance and history references | User/task record; host preserves original references rather than relying solely on an implementer's summary. |
| subject: base/head, workspace, CI config, artifact | Host reads actual Git and relevant staged/unstaged/untracked bytes; use recomputable digests. Artifact identity needs build/target evidence, not just source SHA. The tutorial computes its note/config digests; production artifact proof is not implemented. |
| risk/checks/config/scope/applicability | User/host-approved project policy. PR text, new rule files, comments or tool logs cannot self-authorize a relaxed check. |
| budget, reviewers, human eligibility, allowed actions | User/host planning; carry previous consumption. No universal reviewer/model/cost defaults. An action grant has its own precise binding and expiry. |
| policy SHA and host root | Obtained from a trusted user/host channel and protected outside the reviewed tree; hashing an untrusted policy does not make it trusted. |
| evidence and raw paths | Tool/reviewer input under a separate input root. Host validates actual source, run/SHA/config/scope/time and archives the corresponding raw bytes. JSON validation does not prove the tool ran or the finding is true. |
| authority records | Trusted host transcribes an eligible person's actual decision. Never import approval from evidence or use fixture `grant()` for a real decision. |
| merge/delivery provider | Trusted, explicitly selected adapter plus separate execution authorization. Only isolated Git and local simulation are supplied here; real integrations require additional implementation and validation. |

A missing critical policy/permission/check stays an explicit gap. The default A method needs no runner configuration; optional B must be fully configured before it can issue a meaningful stage recommendation. The runner's gate results do not replace semantic review.

## Troubleshooting by evidence

| Symptom | Read/check next | Appropriate response |
| --- | --- | --- |
| `NEW output directory required` or checkout output refused | The requested tutorial path | Choose a genuinely new external path; preserve old output. |
| Policy pin mismatch | User/host-selected policy bytes and recorded SHA | Establish why it changed; obtain trusted reauthorization/migration rather than reading a new pin from the PR. |
| NOT_RUN/HOLD | Required check list and current round registration | Obtain the missing evidence or authorized capability. Exit 0 alone is insufficient. |
| UNKNOWN | Exact log; subject/config/scope/raw digest, completeness, timestamps and source | Correct provenance or gather a valid new observation with a new ID; retain the old one. Timeout/tool failure is not automatically a product bug. |
| Checks PASS but HOLD | `reasons`, unresolved findings and human decision binding/expiry | Resolve facts or ask the qualified decision owner; do not manufacture approval. |
| Changed version, stale combination or CAS failure | Full source identity, current base, plan and actual ref | Replan/revalidate affected evidence and authorizations. Query uncertain actions before considering any retry. |
| REPLAN / exhausted budget | Inherited and current ledger with counting units | Stop and bring existing evidence to the owner for a revised authorized plan; do not delete/reset or rename the task to evade limits. |
| Delivery NOT_RUN, BLOCKED or insufficient samples | Selected capabilities, artifact/target/cohort/config and finite window | Unselected is not online acceptance; required-but-unavailable is a gap. At window end escalate missing samples; do not silently extend monitoring. |
| Missing Git/Python/POSIX capability or wrapper error | Actual stderr and prerequisites | Report the unavailable capability. No automatic install or fallback to another tool/channel after a host refusal. |

Logs and fixture data are private local execution evidence, not default distribution material. The script has no cleanup routine; remove only your own demo directory when you choose. It never installs anything, connects services, uploads code or establishes recurring monitoring. See [the current workflow and verification scope](workflow.md).
