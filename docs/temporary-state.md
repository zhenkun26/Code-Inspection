# Temporary-state inspection


`save()` normally removes only its own temporary file. A forced exit, SIGKILL or power loss can bypass `finally`, leaving a `.lifecycle-*` file. The file may contain an incomplete or uncommitted ledger or simulated-provider state. Its presence is not an action receipt, and its contents must not be adopted as the current ledger.

From the checkout root, with an explicitly selected trusted host directory and the policy pin supplied by the user/host:

```sh
python3 -B interfaces/lifecycle/cli.py --host-root HOST --policy-sha POLICY_SHA256 temp-report
```

Use `python` on Windows if that is the installed command. This optional interface uses the existing Python/platform prerequisites; it needs no Git, model, service or new dependency. The report is `lifecycle-temp-report/v1` and is not a lifecycle decision or budget report.

- It acquires the existing host lock, verifies the bounded raw `policy.json` bytes against the pin before inspection, and rechecks the pin before returning. It does not parse `state.json`, so missing/corrupt state can still be inspected. Opening the existing lock may create the synchronization file; no ledger, counter or authority is changed.
- It inspects immediate entry metadata without following links. It never reads/hashes residue contents, recurses, deletes, renames, adopts state, retries a provider operation or resets a budget. Symlinks/reparse points, hardlinks, directories, special files and regular files are all retained.
- Enumeration stops at 1,024 inspected root entries or 64 reported matches (one additional iterator entry may be fetched to detect a limit). `complete: false` and `status: UNKNOWN` report a cap or unavailable entry metadata. A scan/policy/lock failure returns CLI exit 2 and UNKNOWN/HOLD instead of a successful inventory. A complete inventory with residues is also UNKNOWN. An empty complete inventory is PASS only for this bounded observation; it does not approve any stage or prove durability. Successful report delivery is exit 0 even when the reported status is UNKNOWN.
- Every residue has `ownership: UNKNOWN`, `action: RETAIN`. No PID, timestamp, age or filename proves ownership or inactivity. The lock excludes participating writers only. POSIX enumeration uses an opened directory descriptor; Windows enumeration is a pathname metadata observation under the existing checked root/lock. Neither is a consistent filesystem snapshot against unrelated host writers; `atomic_snapshot` is always false. Keep the selected root and its ancestors under trusted host control.

Unavailable hardlink counts (including Windows directory-entry metadata) are `null`, not a claim of one or multiple links. JSON escapes unusual filename code points so output remains UTF-8 safe. See [Python directory-entry metadata](https://docs.python.org/3.13/library/os.html#os.DirEntry.stat).

Keep reports private: filenames/size metadata can disclose local information. If state persistence was uncertain, a report does not clear that uncertainty. Preserve the current ledger and authority; use the existing exact-operation query/recovery contract to establish whether an operation was accepted. Never copy a residue over `state.json` or infer success from a matching filename.

**Default inspection still never deletes.** Existing anonymous residues lack verifiable ownership and remain untouched. The optional managed interface below applies only to new registered resources; it does not migrate old files, sweep a prefix, run a daemon or infer ownership from PID/age.

The self-owned tests exercise a real process exit after writing/fsync and before replacement, retained residue, unchanged committed state, ordinary-save controls, missing/corrupt state, pin/lock refusal, bounded enumeration, non-following metadata and native junctions where supported. This does not reproduce actual power-loss durability or authenticate ownership.


### Optional managed resources (trusted host, POSIX only)

The [managed temporary interface](../interfaces/lifecycle/owned_temporary.py) is separate from `temp-report`. It requires the trusted host to affirm that the selected namespace and its ancestors are protected and that **every writer participates in the same host lock**. This is a host integration precondition, not something a JSON record, command-line acknowledgement or filesystem permission check can prove. No input record can enable it. Do not enable it for a directory with unrelated writers. Supply a canonical absolute root; symbolic-link ancestors are refused rather than silently resolved (for example, select the trusted canonical macOS path instead of an alias).

```python
from interfaces.lifecycle.core import Session
from interfaces.lifecycle.owned_temporary import ManagedTemporaryNamespace

# These values and the exclusivity assurance come from the trusted host.
namespace = ManagedTemporaryNamespace(host_root, policy_sha, exclusive_namespace=True)
session = Session(host_root, policy_sha, temporary_namespace=namespace)
# Use session.locked() around the existing authorized lifecycle operations.

# A separate, explicit cleanup grant selects exactly one owner record.
with namespace.locked() as selected:
    result = selected.recover(owner_record_name, authorize=True)
```

No global/personal installation, service, model, Git operation or new dependency is required by this interface. The default Session and three-argument `save()` keep the legacy path; managed registration is opt-in. New managed target basenames start with an ASCII letter or digit and otherwise contain only ASCII letters, digits, dot, underscore or hyphen; Unicode host directories remain allowed. Reserved policy/lock/resource names are refused without relying on filesystem case sensitivity, and existing protected policy/lock identities are checked before staging. The namespace pins a POSIX directory descriptor and validates a regular single-link lock. Managed writers retain that lock through staging, registration, publication and ordinary cleanup. Recovery proceeds only if a fresh process actually acquires the lock; a surviving child or other holder can still block it. PID reuse, reboot time and file age are not inactivity evidence. The host must validate the filesystem’s locking and fsync semantics; local tests do not certify every POSIX or network filesystem.

Only a newly exclusively created, fully written and fsynced staging payload can obtain a bounded versioned owner record. The record binds the policy pin, root identity, exact random staging name, intended flat target, object identity/metadata, size and content digest. Registration is not build provenance, a lifecycle receipt or cryptographic ownership against a malicious writer already controlling the trusted root. A crash before complete registration can still leave an unknown residue; a torn, missing, inconsistent or concurrently changed record must be retained. A self-consistent record recreated by code with trusted-root write access cannot be distinguished from a genuine host record; the trust boundary still applies.

Recovery validates the exact selected record and payload under the lock, with bounded no-follow reads, type/single-link checks, root/policy binding and identity/content checks. It never reads arbitrary legacy payloads, traverses recursively, copies staging data into the ledger, resets budgets or retries a provider. It removes a qualifying payload before its registration. Missing payload does not establish whether publication succeeded and must never redirect deletion to the intended target. Errors or uncertain persistence are not successful cleanup or business receipts; retain the existing Session persistence-failure fence and use the normal exact-operation query/recovery protocol.

**POSIX boundary:** descriptor-relative operations and rechecks do not provide atomic “unlink this inode only” protection against a nonparticipating writer replacing a name between verification and unlink. The explicit trusted-namespace precondition is therefore mandatory; without it or without the exact cleanup grant, no deletion is authorized. Never present these tests as proof of safety against a hostile same-host writer. SIGKILL cannot run `finally`; cleanup happens only on a later explicit safe recovery attempt, and some early/uncertain residues remain.

**Windows boundary:** the new managed write/recovery capability fails closed. The existing Windows reader/lock is not an identity-bound deletion backend. Native managed recovery is not implemented; the separate [Windows API experiment](windows-temporary-qualification.md) observes system calls but does not qualify product recovery or power-loss persistence; legacy save and metadata-only `temp-report` remain unchanged. Do not interpret skipped POSIX cases as Windows cleanup acceptance. No current user residue is removed by adopting this version.
