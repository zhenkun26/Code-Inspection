# Security policy

This project is under active development. Security reports should identify an exact revision and supported environment; there is no separate maintained release series or promised response time. See [platform limits](docs/windows-support.md), [acceptance evidence](docs/acceptance.md) and [trust boundaries](docs/workflow.md).

The local runner is not a sandbox, identity provider or production deployment service. Host policy/authority directories must be trusted. Only explicitly authorized, self-owned fixtures should be executed. A simulation, test result or agent statement does not authenticate an approver or grant new permissions.

## Report a vulnerability privately

Do not post exploit details, credentials, private code or raw logs in public issues, PRs or Actions output.

If GitHub offers **Report a vulnerability** in this repository's Security tab, use that private reporting flow. Its availability is not assumed by this policy. If it is unavailable, open only a minimal, non-sensitive issue asking the maintainer to establish a private reporting channel; do not include the vulnerability details until that channel is agreed. No private mailbox or reporting integration is configured by this document.

Through the agreed private channel, provide the affected commit, platform, minimal safe reproduction, expected/actual behavior and impact. Use synthetic data and remove tokens and personal paths. Do not test against other users, production systems or third-party services without their authorization.

An accidentally published secret should be revoked or rotated with its issuing service; deleting a visible file alone is insufficient. Coordinate history or artifact remediation with the maintainer rather than rewriting or deleting evidence unilaterally.
