# SPQR Bridge v2 — Jimmy's decisions

Recorded by iggy from Jimmy's rulings, 2026-10-21 ~00:05 EDT. These bind the Phase 1 build.
Builder: Codex, against `docs/DESIGN-V2.md` (Phase 0 design, passed round-4 independent review).

## The product, in Jimmy's words
"I tell an agent a task and it has the ability to task others."

## Decision 1 — Open roster (agents that can be tasked)
- Start with what Jimmy already uses: **Claude, Codex, OMP** — enabled as taskable agents.
- **OpenCode** is next; Jimmy expects to want it soon.
- The roster is **open by design**: adding any agent later (Crush, Pi, Goose, or a tool that doesn't exist yet) is a registry/adapter configuration change, never a rebuild. OMP may itself be asked to use Crush or another agent — chained delegation follows the same registry, caps, and depth rules.
- "Add everyone / anyone": any agent in Jimmy's stable can be added, as a taskable agent and as a principal. Deny-by-default still applies to anything NOT in Jimmy's registry — "anyone" means anyone of Jimmy's, not the open internet.

## Decision 2 — Principals (who can use the bridge)
- Any agent Jimmy designates may be a principal — his Caesars, iggy, and any stable agent he names. Adding a principal is a registry entry Jimmy owns; no implicit authorization from a profile or role name (per the design).

## Decision 3 — Global skill
- The delegation skill (`spqr-agent-delegation`) ships **globally**: installed by default for every harness and profile Jimmy uses, not copied per-Caesar.
- Onboarding a new agent = two steps only: (1) the global skill (it knows how to delegate), (2) a registry entry (it's allowed to). The skill teaches *how*; the registry and caps still decide *who may* and *how much*.

## Decision 4 — Scope is set by Jimmy at the time of use (2026-10-21 00:06)
- Permissions are **not standing grants**. What a delegated job may do — read vs. write, which workspaces, which agents it may task in turn, how much it may spend — is **scoped by Jimmy at the time he gives the task**, per job.
- The registry therefore carries identity (who exists, who may authenticate) and at most outer ceilings — never a standing scope. A job carries Jimmy's scope with it, and no agent may exceed or widen it; a sub-delegation inherits a scope no broader than its parent job's.
- Where Jimmy gives a task without spelling out scope, the job runs at the minimum (read-only, tightest cap) — silence is never a broad grant.
- **Design amendment:** DESIGN-V2.md assumes registry-carried per-principal policy. This ruling supersedes that: Phase 1 must support per-job scope issued by Jimmy at task time. Codex folds this in before building; the amendment goes through the same review gate as the rest of Phase 1.

## Decision 5 — The limit is usage (2026-10-21 00:08)
- There is **no separate monetary cap layer** in the bridge: no per-job dollar ceilings to configure.
- The outer limit is **usage** — the underlying accounts' usage limits (subscription windows/quotas) are the wall. A job runs within Jimmy's at-use scope until the work is done or usage runs out; usage exhaustion is a normal stop, reported as such (as happened 2026-10-20 when Ledger's run and a parallel Codex project drained the shared 5-hour window).
- Consequence, accepted by Jimmy: jobs share one usage pool, so parallel work competes for the same window. The bridge's job is to report usage-stops honestly, not to ration.
- **If it runs out, stop** (Jimmy, 2026-10-21 00:08): usage exhaustion is a hard stop for the job — no automatic retries, no falling back to per-token/paid routes, no switching providers to keep going. The job reports that it stopped on usage and waits for Jimmy.

## Decision 6 — Loop guardrails: 3–5 for failure, 5–10 for progress (2026-10-21 00:08; split proposed by iggy 00:09, CONFIRMED by Jimmy 00:10 — "i trust you, go with the split")
- Separate from money: **loop/retry guardrails exist** — an agent may not spin forever.
- **Failure retries — cap 3–5.** Retrying the same step with the same approach caps inside 3–5 attempts, then the job stops and reports what's blocking it. (Jimmy's band; matches his standing leash: "if stuck after 3 tries, stop and tell me." More attempts burn the shared usage window without fixing anything.)
- **Progress loops — cap 5–10.** Iterative work where each round produces new state (build→test→fix cycles, research passes) may run 5–10 rounds. Tripwire: an iteration that produces no new state — same error, same output — kills the loop early regardless of the counter.
- The builder picks the exact number per mechanism within each band and documents each one. Delegation depth stays bounded on the same principle (v1 shipped depth 1); any depth change is a scope Jimmy sets at time of use, inside the guardrail bands.

## Still open (iggy's proposed defaults — NOT yet ruled by Jimmy)
- Revocation: the design's hard default (refuse new calls, cancel queued, stop running, withhold undelivered results).
- Legacy v1 jobs: unassigned/inaccessible unless Jimmy approves a mapping.
- Cutover to the Phase 2 daemon, and any GitHub push: Jimmy's order only.
