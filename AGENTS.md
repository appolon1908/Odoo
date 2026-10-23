# Codestra Agent Governance Standard

Every task belongs to Product → Section → Subsection → Atomic Task.

Valid promotion path only:

atomic task → subsection branch → section branch → development → testing → staging → production

No other promotion path is accepted.

## Ownership and leases

Each active subsection has exactly one implementation owner. Review, test, certification, and investigation agents may assist but must not independently edit the same implementation scope unless explicitly assigned.

Allowed states: NOT_STARTED, CLAIMED, IN_PROGRESS, BLOCKED, REVIEW, COMPLETE, CERTIFIED.

Abandoned leases may be reclaimed only after the configured heartbeat timeout and worktree inspection.

## Mandatory pre-work synchronization

Before editing: fetch/prune origin; verify workstation and parent branch; record HEAD SHA; verify clean tree; record ahead/behind; synchronize safely from parent; run preflight; verify production-effect gates remain disabled.

Stop on unexplained local changes, divergence, invalidated base assumptions, conflicts, missing required dependencies/credentials, or uncertain safety. Never overwrite unknown local work.

## Atomic implementation

Every atomic task includes applicable implementation, tests, error handling, contracts, documentation, migrations, observability, and security review. Do not combine unrelated changes.

## Code quality

Completion is prohibited with placeholder code, TODO-as-implementation, dead/duplicate code, temporary bypasses, broad exception swallowing, hard-coded credentials, production secrets, unexplained lint suppression, disabled tests, skipped security checks, or temporary production flags.

## Test before push

Run applicable formatting, lint, types, unit, integration, contract/OpenAPI, migration, security, secret scan, and git diff --check gates before every checkpoint push. Do not knowingly push broken code.

## Push discipline

After each atomic checkpoint: validate, commit intended changes, push branch, verify remote SHA, update mission status, record blockers. Do not end with unexplained uncommitted implementation work.

## Commit standard

One understandable unit per commit. Avoid meaningless messages such as update, fix, changes, or stuff.

## Parent synchronization

Before merge request: fetch remote; compare parent; integrate current parent safely; resolve conflicts intentionally; rerun required tests; push refreshed exact branch; require CI on the new exact SHA. Old CI evidence does not certify changed code.

## Protected branches

Do not directly develop on main, development, testing, staging, or production. Protected branches move only through approved pull requests and required checks. Force pushes are prohibited.

## Production effects default OFF

Unless a separately approved production activation mission changes them:

PRODUCTION_GO=NO
LIVE_CAPABILITIES_ENABLED=NO
EXTERNAL_EFFECTS=false

Implementation missions must not silently enable calls, SMS, email, WhatsApp, payments, social publishing, production database mutation, production infrastructure changes, credential issuance, or external media publishing.

## Completion evidence

Every completed subsection records final branch/SHA, parent SHA, changed files, tests/results, security result, migration result, API/contract result, dependency changes, limitations, remaining TODOs, CI result, and reviewer result. Missing evidence means not COMPLETE.

## COMPLETE

COMPLETE requires implementation present, applicable local/integration/contract/security tests green, current documentation, valid migrations, branch pushed, clean tree, local/remote SHA match, and no unresolved blockers.

## CERTIFIED

CERTIFIED additionally requires exact-SHA CI green, independent review, parent integration success, regression/security/performance gates as applicable, no unresolved critical/high defects, and stored completion evidence. Only CERTIFIED work may promote.

## Cleanup

After parent integration, verify exact intended changes are present, remove obsolete temporary worktrees/locks, archive evidence, prune stale local refs, preserve unmerged branches, and retain recovery references where required.

## Fail closed

When safety cannot be proven, stop and mark BLOCKED. Never guess around secrets, migrations, authorization, branch ancestry, production effects, destructive operations, or incomplete CI evidence.

These additional repository instructions supplement the governance standard above and do not relax its protected-branch, promotion, or production-effect requirements.

## Repository Agent Instructions

This `AGENTS.md` applies to the entire repository unless a deeper `AGENTS.md` provides more specific instructions for a subdirectory.

### Ownership and repository identity
- The canonical GitHub namespace for this repository is `appolon1908`.
- Treat this repository and its current default branch as the authoritative remote state. Do not assume an older owner or transferred repository path is still canonical.
- When touching cross-repository references, verify the dependency exists under `appolon1908/<repo>` before changing URLs, package sources, submodules, CI references, or documentation.

### Working rules
- Preserve repository history, current architecture, public APIs, and compatibility unless the assigned task explicitly requires a change.
- Use one active task branch/worktree per implementation lane.
- Do not perform feature development directly on a protected default branch unless the repository workflow explicitly requires it.
- Never force-push, rewrite shared history, or discard another contributor's uncommitted work.
- Before publishing changes, verify the exact repository, branch, upstream, remote HEAD, and working-tree state; fetch the target branch first.
- Do not create duplicate engines, services, adapters, schemas, or sources of truth when a canonical implementation already exists.

### Implementation and verification
- Implement requested behavior completely and add or update tests for changed behavior.
- Run the relevant unit, integration, lint, type-check, build, API/OpenAPI, Postman, or end-to-end checks that exist in this repository.
- Do not disable, delete, bypass, or weaken CI/security checks merely to make a pipeline green; fix the underlying issue.
- Keep code, schemas, generated contracts, OpenAPI definitions, migrations, and tests synchronized when a change affects them.
- Preserve backward compatibility unless a breaking change is explicitly approved and documented.

### Security and production effects
- Never commit passwords, tokens, API keys, private keys, certificates, production credentials, or other secrets.
- Default-deny live external side effects such as payments, payouts, phone calls, SMS, email, WhatsApp, social publishing, or provider mutations unless the task explicitly authorizes production effects.
- Do not bypass authentication, authorization, tenant isolation, policy, idempotency, audit, reconciliation, or safety gates.
- Keep production and staging credentials outside source code and test fixtures.

### Handoff requirements
- Report the branch, exact HEAD SHA, files changed, tests/checks run, results, and remaining blockers.
- Distinguish clearly between code-ready, merge-ready, staging-ready, and production-ready states.
- Do not claim completion, certification, deployment, or production readiness without evidence.
