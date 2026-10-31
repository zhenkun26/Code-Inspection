# Optional trusted-host delivery provider

The bundled provider still modifies only local simulation state. A trusted Python host may separately supply a provider for **deploy and rollback**. This interface validates policy selection, intent, content and correlated receipts; it does not contain a production connector, credentials, remote authentication or a deployment service. Its tests use synthetic provider objects and real isolated Git/file fixtures.

## Selection and permissions

The default CLI installs no provider. The embedding host uses `Session(host_root, policy_sha, delivery_providers={provider_id: provider_object})`; the object implements `read(context)`, `apply(request, snapshot)` and `query(request)`. It is trusted application code, not sandboxed code. The host controls transport, credentials, cancellation, bounded callback execution, remote identity, conditional updates and durable idempotency. This package cannot prove those guarantees by checking a returned JSON object.

An optional top-level `delivery_provider` in the pinned policy selects exactly:

```json
{
  "schema": "host-delivery-policy/v1",
  "provider_id": "host-selected-provider",
  "provider_version": "reviewed-implementation-v1",
  "target": "host-selected-target",
  "max_age_seconds": 300,
  "guarantees": ["conditional_apply", "durable_idempotency"]
}
```

The guarantee list is an exact set with no duplicates; order is irrelevant. These values illustrate fields, not approved defaults or a working connection. The host must establish both guarantees before opting in. Changing provider implementation, target or trust meaning requires a new selection/version and policy pin. Reviewed records cannot select imports, shell commands, URLs, credentials or a registry. No automatic install or connection occurs. Missing capability is UNKNOWN/HOLD, never simulation fallback or successful production acceptance.

The existing delivery module/capability selection, exact human authority, artifact policy, optional build-provenance policy and continuous budget remain authoritative. Host-provider deploy/rollback always require a supported actual single-file artifact, even when legacy simulation permits labels. The other real actions (flags, promotion, observation, pause and forward-fix) are not implemented in this interface; they cannot silently run against `sim-provider.json`. Existing simulated actions remain available when no host provider is selected.

## Read, intent and result

`read(context)` receives only the task, policy pin and selected provider/version/target. It returns a strict `host-delivery-state/v1` with those provider selectors, an opaque revision token, artifact, cohort, config, data version, explicit enabled/paused booleans, capabilities and observed time. A read must be current and for the exact target. The snapshot digest excludes observation time so a new read of unchanged state does not manufacture drift. All substantive fields remain bound.

A `host-delivery-proposal/v1` binds the full source subject, validated merge source, content proof, applicable build-verification proof, policy, provider selection, expected state and action effects. Its ID is the approval binding. Deploy uses the current confirmed merge; rollback uses a specified confirmed historical merge and compatibility with the current data version. Config and revision remain actual provider facts, not generated `local-config-N` values.

`apply(request, snapshot)` receives a copied immutable-intent `host-delivery-request/v1` and the exact bounded `artifacts.Snapshot` already checked. It must consume those bytes, not reopen the artifact locator. The request includes the operation/proposal ID, task/policy/provider/target, action, parameters, expected state/token, required effect, content proof and request digest. The provider must enforce that precondition and persist the operation's idempotency association atomically with its own action. A read followed by an unconditional remote write is not a compliant adapter.

`query(request)` must observe this exact operation without initiating it. Merely finding the same artifact at the target does not prove that the requested operation occurred.

A strict `host-delivery-result/v1` binds operation, request digest, provider/version/target and observation time. Status is `confirmed`, `pending`, `unknown`, `not_applied` or `failed`. Confirmed results additionally contain actual `after` state and consumed SHA-256/length. Validation requires the requested artifact, target, cohort/data and disabled exposure postconditions, changed revision and exact consumed bytes. Deploy begins disabled/unpaused; rollback begins disabled/paused. A contract-valid result remains an assertion from the trusted host provider, not independent identity authentication or health proof.

## Persistence and recovery

Fresh execution requires the separate persistence capability described below before charging. It checks policy, current subject, authority, source/evidence/provenance, compatibility and target state, acquires immutable artifact bytes, saves the charged attempt and pending intent, and obtains a matching operational checkpoint before one `apply` call. Missing capability is UNKNOWN/HOLD without charge or apply. A failed local save or unconfirmed checkpoint fences the Session; an in-memory update does not establish persistence.

Every existing attempt is **query-only**, including a provider's `not_applied` result. No automatic resend, replacement idempotency key or repeated charge occurs. An unresolved same-target attempt blocks a new plan and keeps stage evaluation on HOLD, even when current state matches an older successful receipt. This prevents retries disguised as new intent and prevents old evidence from hiding an unknown newer outcome. A genuinely new retry needs an explicit workflow with new reviewed intent and approval; deleting history or resetting budgets is not such a workflow.

Only strictly bound terminal results are cached. Cached historical outcomes remain readable without provider availability, fresh artifact files, fresh authority/provenance or remaining budget. They explicitly leave current state UNKNOWN and do not claim renewed health/authentication. Query can recover a missing correlated receipt after a lost response even when that immutable receipt is older than the current-state freshness window; its identity and time ordering still must match. This is historical recovery, not a fresh state assertion. Query cannot overwrite contradictory recorded outcomes. Ordinary callback failures are sanitized; cancellation propagates with the recorded pending attempt retained; its storage guarantee remains limited to the host-qualified checkpoint and backend contract. Provider timeouts/cancellation and termination of external work remain host responsibilities.

A host-selected policy cannot start a fresh legacy simulation action; existing exact attempted simulation recovery remains available. Old v2–v4 simulation plans keep their original validators and recovery path; they are not converted or relabeled real. Adding the optional policy block changes the pin, so an existing ledger cannot silently adopt it. Use the original pin for old historical recovery; any migration is a separately reviewed host action and cannot invent new approval.

### Host persistence checkpoint

The default `save()` fsyncs its staging file and replaces `state.json`; it does not sync the containing directory. A successful save or policy-checked locked reload establishes local visibility. The compatible accounting label `durability=persisted` has that scope only; `durability_scope.power_loss` remains `unknown`. POSIX managed saves additionally synchronize their directory, conditional on the host's filesystem/locking assumptions. Neither backend automatically qualifies external execution, and Windows managed cleanup remains disabled.

For a **new** host-provider action, the pinned policy must select a `persistence` record:

```json
{
  "schema": "host-persistence-policy/v1",
  "adapter_id": "host-selected-checkpoint",
  "adapter_version": "reviewed-implementation-v1",
  "storage_target": "host-selected-ledger-store",
  "config_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "qualification": "host-reviewed-storage-contract-v1"
}
```

This is a field example, not a qualified backend. The embedding Python host additionally supplies `Session(..., persistence_adapters={adapter_id: adapter})`. The object exposes matching selection fields and implements `checkpoint(binding, state_bytes)`. Policy text, a JSON result, a boolean or a qualification reference alone cannot supply the capability. References are identifiers, never automatically opened files, imports, URLs or shell commands. Changing the selection changes the policy pin and requires reviewed intent/approval; existing ledgers are not silently migrated.

While the Session lock is held, the implementation reads bounded saved ledger bytes, checks them against the intended pending state and policy, and supplies immutable bytes plus copied metadata. The `host-persistence-checkpoint/v1` binding covers adapter/version/storage/config/qualification, task/policy, ledger root identity and locator, operation/request, exact byte digest/length and a fresh challenge. The root locator is private host context, not a public receipt. The adapter must perform its actual storage checkpoint, then return exactly `host-persistence-confirmation/v1`, `status: confirmed` and the unchanged binding. Selection, root, policy and saved bytes are rechecked before apply. A confirmation from another invocation or snapshot is insufficient. Confirmations are not cached to authorize future actions. After a potentially slow checkpoint, local PR eligibility, the already-read provider observation, the current verifier observation's time window and exact human authority are checked again before apply. The verifier is not called twice: its fresh observation is retained only in memory, while the approved historical proof stays unchanged. Expiry or revocation leaves pending query-only and fences the Session.

The host must qualify the implementation and target storage, including the effect of subsequent ordinary receipt replacements and restart recovery. The host also bounds callback execution and cancellation; the interface cannot forcibly terminate arbitrary trusted Python code. Merely echoing the binding, calling a no-op flush or naming a storage contract does not implement that guarantee. This package checks the handshake and consistency; it cannot authenticate or sandbox trusted Python code, certify hardware, or prove the host's guarantee from JSON. The included adapter is a synthetic test fixture only; no production persistence adapter or service is bundled.

A missing/mismatched adapter stops before charge. Failure, cancellation, unconfirmed/malformed confirmation or changed saved state after pending publication prevents apply and fences the Session. A successful locked reload can restore read/query access; a recorded pending operation remains query-only without another charge, even if the persistence adapter, current authority or artifact is unavailable. It is not permission to resend. Local save failures retain their existing uncertainty handling. Checkpoint confirmation does not turn later receipt save/reload visibility into a universal power-loss guarantee.

### 简体中文：持久化边界

普通保存对暂存文件执行 fsync 后替换账本，没有同步父目录；`persisted` 兼容标签只说明保存成功或持锁重载可见，机器可读范围仍将掉电持久性标为 unknown。新外部交付动作必须由受信政策选择、Python 宿主实际注入具有匹配身份/配置/资格范围的检查点实现；任意布尔值、自填报告或资格名称不能授权。

缺能力时在收费前 UNKNOWN/HOLD；pending 保存后，以精确字节、根身份、政策、操作、请求及本次随机挑战绑定实际检查点，确认与重新读取一致后，还需复核本地 PR 条件、已有 provider 观测、本次核验结果有效期及精确真人批准，再调用 apply；本次核验结果仅保存在内存，不重复调用核验器或改写批准的历史证明。适配器错误、取消、错关联、未知结果或期间变化都会阻止 apply，并保留 Session 失败隔离。宿主必须证明所选存储实现的适用保证，包括后续回执替换和恢复，并负责回调时限及取消；接口不强制终止任意受信 Python 代码。合成 fixture 只验证接口，不证明真实存储或掉电安全。

旧账本不升级保证、不清预算、不沿用新政策下的批准。已尝试操作及合法历史回执仍可按原身份查询，不因缺新适配器、预算耗尽或当前批准/产物缺失重复执行或收费。清理不能解除保存失败隔离，Windows 产品清理继续禁用。默认 Markdown Skill 和本地模拟不需要此可选能力，也不安装服务、模型或新运行依赖。

## Verification and limits

Use the existing maintained checker and fixed mutation checks. New fixtures cover exact binding, missing capabilities, unsupported actions, immutable consumption, drift, failed/unknown/cancelled calls, persistence faults, historical recovery and no replay/charge. They make no production calls. Actual provider acceptance still requires the user's selected target, authenticated transport, permissions, conditional-write/idempotency implementation, independent outcome readback and negative/failure tests in an authorized environment.

No vendor, service purchase or new credential is required by the default Skill. Without a configured and validated provider, real execution remains unavailable; this interface does not turn the Skill into a hosted delivery platform.
