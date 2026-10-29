# Agent J Harness — upstream map (AGENTJ-CORE-SPEC §5)

Design and text are ported, not code copied; each module names its source. Monthly `core-upstream-sync` compares the locked
commit with the latest on these paths only.

| core module | upstream | path | locked commit | license |
|---|---|---|---|---|
| tools.py (descriptions, parameters, 2000 lines / 50 KB truncation, multi-edit exact replacement) | earendil-works/pi | packages/coding-agent/src/core/tools/{read,write,edit,bash,grep,find,ls,truncate}.ts | 8efd314fa3d3232ce197d8d7a6a5660530b9d83e | MIT |
| prompt.py (preamble, tool snippets, rules) | earendil-works/pi | packages/coding-agent/src/core/system-prompt.ts | 8efd314fa3d3232ce197d8d7a6a5660530b9d83e | MIT |
| loop.py, llm.py (step loop, retries, reasoning replay flag) | earendil-works/pi | packages/pi-agent-core, packages/pi-ai (openai-completions) | 8efd314fa3d3232ce197d8d7a6a5660530b9d83e | MIT |
| pipeline.py (pre-execute order), loop.py (result trimming) | deepseek-ai/deepseek-harness | design only | dsh-v0.2.1-alpha.4 | MIT |
| loop.py (minimal loop) | SWE-agent/mini-swe-agent | design only | — | MIT |
| orders.py (standing orders, brief fields, decision log, retry by failure kind, gates with defaults); since 0.26.0 also the default path: ../decisions.py (`agentj decide`, the shared decision-log format, retry table and root-cause hash) | cursor/plugins pstack (Lauren Tan) | rules and data shapes only (P359 review of pstack; P571 review at 6f6c9e6) | 0.15.25 · 6f6c9e6 | MIT |
| presets.json roles / fallback / edit_mode, loop.py output_schema, pipeline C14 | can1357/oh-my-pi | ideas only (P359 review of oh-my-pi) | v18.11.x | MIT |
| orders.py preflight, blocked-by, root-cause hash | EricTechPro/super-board | criteria only (spec §14) | — | MIT |

Not used: Crush (FSL) — not one line; Claude Agent SDK — not open source.
