# Google Harness² and SPQR

Sources: github.com/google-research/harness2 (Apache 2.0, v0.1.0), arXiv 2610.14224,
MarkTechPost write-up (2026-10-18). Read 2026-10-22.

## What it is

A **harness** is everything around a model that makes it an agent. Harness² treats it as 8
editable files: system prompt, guardrails, memory, tool guidance, subagents, skills, scripts,
plugins. Model weights never change. Harness² improves the harness while the agent works:

1. **Task level: Propose, Probe, Compose.**
   - Run the task once with the base harness (the reference).
   - An editor model proposes 2 edited harnesses: a *general* fix (planning, tool use) and a
     *domain* fix (knowledge and procedure for this kind of task).
   - Both run on the same task (the probes).
   - A composer compares the runs and keeps only edits that observably helped. No answer key
     needed.
   - `k` widens it (parallel: k candidates of each) or deepens it (sequential: k rounds).
2. **Across tasks: reflection.** After each task a reflector saves the adopted harness and
   lessons to `EXPERIENCE.md`, and that library steers the next task's proposals.

Executors are **OpenCode** (default) and **Codex**. The editor in the paper was Claude Opus.
Models are called through Vertex AI by default. One step (k=1) improved every setup tested, by
up to 11.5 points. Weaker models gained the most: GLM-5 under OpenCode overtook base
Gemini 3.5 Flash. Cost: about 4 runs per task at k=1.

## Why it fits SPQR

SPQR already has the pieces, under different names:

| Harness² | SPQR |
| --- | --- |
| Executor (OpenCode, Codex) | Body agents run by the bridge (claude, codex, omp, opencode…) |
| Editor, composer, reflector | The Brain |
| Probes run in parallel | Bridge jobs (bounded, read-only by default, concurrency 2) |
| `EXPERIENCE.md` + adopted harnesses | Shared memory library |
| 8 harness components | Each agent's own files: AGENTS.md/CLAUDE.md, skills, MCP config, hooks |

## How we'd implement it

**Recommended: build the pattern into SPQR rather than run Harness² itself.** Harness² is a
benchmark framework: Python, Vertex AI, its own runner, Codex with its sandbox off, and
unrestricted network. We'd keep our bridge's safety model and any-agent design.

1. **Harness as a folder per Body member.** Store each agent's harness (the 8 parts, mapped to
   that agent's native files) in shared memory, versioned. The bridge mounts it into the job's
   workspace.
2. **A "Refine" option on Give task.** The Brain writes the 2 candidates. The bridge runs the
   reference and both probes as 3 jobs in separate scratch workspaces. The Brain composes, then
   the final run produces the deliverable. `k` is a setting.
3. **Reflection into shared memory.** After each task the Brain writes an Experience note
   (lessons, which edits helped) and saves the adopted harness. Future proposals read them.
4. **In the Bridge window:** probes show as branches under the Body member, with a side-by-side
   view of what changed and why it was kept.

Gaps to close first:
- **Trajectories.** The composer needs each run's full transcript. The bridge only returns the
  result and a stderr tail today, so it must capture agent session logs.
- **Round caps.** The bridge's loop guards (`no_progress`, round cap per hour) would block probes,
  so refine runs need their own grant.
- **Cost.** About 4 runs per task, so it should be opt-in per task.

**Quick experiment first:** run Harness²'s offline demo (`harness2-demo`, no credentials) to see
the file layout and outputs, then do one SPQR task by hand with k=1 before writing any code.

## Decided (meeting, 2026-10-22)

- Automatic task experiments come first, with one bounded cycle per selected task and the same
  model across every comparison.
- **Keystone tasks always get an experiment cycle; rocks don't.** Keystones are the most complex or
  most crucial tasks. Rocks are routine work and just run. The meeting marks each task as one or
  the other when it sets the goal and budget, and a rock can be promoted if it turns out harder
  than expected.

## Proposal for the next meeting: each role is a homepage

From the "Agentic OS" doc (a 2-page framework that splits one agent into 7 layers). The idea for
SPQR: **every role (Caesar through Jules) is a folder with the same 7 layers, written once in a
neutral format.** It's the role's homepage: whichever agent fills the slot starts there and gets
the same direction, rules and memory.

| Layer | SPQR today |
|---|---|
| Identity | each role's rules |
| Knowledge | the Library of Alexandria, loaded only when needed |
| Memory | shared memory and lessons |
| Workers | the Body agents via the bridge, each isolated |
| Automation | skills, hooks, the meeting ritual |
| Guardrails | the bridge's grants, read-only default, hard stops |
| Evaluation | **the gap**; this is what the Harness² pattern above adds |

SPQR translates the folder into whatever the filling agent reads (CLAUDE.md, AGENTS.md,
GEMINI.md, opencode or omp config), so any agent can step into any slot, Caesar included. This
fits the "harness as a folder per Body member" step above, widened from Body members to every role.

Questions: which roles get a homepage first, and where the folders live (shared memory or the
bridge repo)?

## Questions for the meeting

- Which agent is the default editor/Brain for refinement?
- Should adopted harnesses be per agent, per project, or both?
- Is ~4× cost per refined task acceptable, and for which tasks?
