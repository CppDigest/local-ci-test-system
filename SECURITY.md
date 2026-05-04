# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| main    | Yes       |
| v0.1.x  | Yes       |

Older branches or tags not listed above do not receive security fixes.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report security issues through **[GitHub private vulnerability reporting](https://github.com/cppalliance/local-ci-test-system/security/advisories/new)** (preferred), or email **security@cppalliance.org** if you do not have a GitHub account.

Include as much of the following as you can:

- A description of the vulnerability and its impact
- Steps to reproduce or a proof-of-concept
- Affected version(s) or commit range
- Any proposed mitigation or fix

You can expect an acknowledgement within **5 business days** and a resolution or status update within **90 days**.

## Scope

The following classes of vulnerability are **in scope**:

| Category | Examples |
|---|---|
| Container escape | Breakout from Docker containers spawned by `localci run` |
| Token / secret exposure | CI tokens, Docker registry credentials, or `.localci.yml` secrets leaked to logs, temp files, or child processes |
| Path traversal | Workflow YAML or config paths that escape the project root |
| Dependency confusion / supply chain | Malicious images resolved by the image-scoring algorithm |
| Privilege escalation | Commands that gain unintended host-level access via Docker socket |

Issues with the act runner itself, Docker Engine, or other external prerequisites should be reported to their respective projects unless the vulnerability is triggered specifically by Local CI's orchestration of those tools.

## Out of Scope

- Vulnerabilities in third-party dependencies (act, Docker, yq) that are not amplified by Local CI
- Denial-of-service issues with no security impact
- Theoretical vulnerabilities without a realistic attack scenario
