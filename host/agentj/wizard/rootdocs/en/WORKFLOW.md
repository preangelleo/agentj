# Workflow — how every request moves

<!-- agentj:core v1 — core ideas: maintained by Agent J upgrades; do not delete or edit; add your own outside this block -->
## Every request: where an intention settles
1. A one-off → done on the spot or by a junior assistant; nothing to keep.
2. Repeated work → a **workflow**: one direct child folder of the working root, a code name (lowercase-hyphen), a full document
   set, the skills, command-line tools and MCP it needs, its own CEO and sub-Agents, scheduled jobs as needed.
3. A capability, not a process → a **skill**: global when several workflows use it, local inside the one workflow it serves.
4. Stable, deterministic steps → a **command-line tool**, so code runs them and the model does not re-think them every time.
Ask the Chairman once before building; build after a yes. Work that already has a CEO: look it up in the ROLES.md roster and
hand it to that CEO (sub-session, headless or scheduled); never touch its files yourself.

## New workflows follow the Workflow Design Bible
- The method is the open-source Workflow Design Bible (MIT licence, https://github.com/preangelleo/workflow-design-bible).
  Agent J ships its cut-down skill **agentj-workflow-wizard** in the package and installs it in the working root — no download.
- What every workflow gets: a thin entry file (CLAUDE.md / AGENTS.md, pointers only) + a constitution with red lines + a few
  short boot documents + a STRUCTURE manifest + its own CEO. One structure for all is what lets the chief of staff run dozens or
  hundreds of them.
- Files land only through the wizard: stage them in `.agentj/wizard-staging/`, then `agentj wizard apply` and
  `agentj wizard doctor`. Files the Chairman changed are never overwritten. Then register it in the ROLES.md roster and
  STRUCTURE.json.

## Dispatch, read the report, rework
- Dispatch with the Chairman's own words and the evidence you found.
- Read the actual report and its closing VERDICT: ok | attention | fail. Exit codes alone prove nothing; a missing or skipped
  report is unfinished work.
- On failure: send the evidence to the owning CEO for repair and a rerun, read the new report, then close it. Fix the same
  mistake at its source (document, skill or command line) so it does not happen twice.

## Morning brief
Read every overnight report and give results and pending decisions only: what got done, what failed, what the Chairman must
decide. No report means saying so, never inventing one.

## A new computer with no workflows yet: how to start
A fresh Agent J has no workflows of its own; the first job is finding the first thing worth handing over:
1. Ask the Chairman: "What are three things you do again and again every week?" (for example competitor news, customer
   emails, a weekly report, social posts).
2. Pick the one that repeats most and is easiest to check, and propose it in one line: "This can become a workflow: a
   weekly-report folder with its own CEO and documents; I run it once by hand for you, and it goes on a schedule only after
   you say yes."
3. A yes → build it with the agentj-capability skill (the wizard's goal mode) → run it once → read the report → tell the
   Chairman the result.
4. Once it works, ask about the next one. Goal for the first month: two or three workflows really working for the Chairman.

## Handover standard: anyone can leave at any moment
Write every workflow's documents as if the whole staff resigned this minute and a new team arrives the next: a fresh session
that reads only the entry and the boot set can take over. Each round ends with NEXT_SESSION rewritten (work in flight, where it
stopped, the next step); decisions go into the constitution or MEMORY.

## Maintaining the documents
- Any change to the documents here or in a workflow keeps the constitution's core: one folder, one job; documents short enough
  for a newcomer to finish; facts in files; on conflict the core prevails.
- **Review after a model upgrade**: rules written for trainees hold graduates back. After each major model upgrade every CEO
  reviews its workflow's documents, drops steps and repeated reminders the new model no longer needs, keeps red lines and
  facts; the chief of staff checks the result.
<!-- /agentj:core -->

## The Chairman's part (yours to change)
- Morning brief time: (for example 8:00 every day)
- Other habits:
