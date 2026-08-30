# AGENTS.md — AI Agents and SKILLs

## Project Overview

Building blocks for AI personal-assistant agents: each **bundle** pairs a
deterministic Python package with the SKILL that drives it.

Code does the deterministic work — fetching, ETL, maths, dedupe, state tracking,
rendering. The model does the judgement work: what is worth saying, how to say
it, and the calls the tools deliberately refuse to guess at. Everything the model
says about the data comes from a payload the tools returned; the model never
computes a number.

### Bundle anatomy

```
<bundle>/
  pyproject.toml           uv project, console script entry point, pytest config
  <package_name>/          deterministic Python: cli.py, db.py, fetch/scan, render
    config/                shipped data (sources.json etc.) as package-data
  skills/<skill-name>/
    SKILL.md               small: name, description, setup, command surface
    references/            domain detail, opened only when a task needs it
  tests/                   pytest, fixtures under tests/fixtures/
  docs/DESIGN.md           design notes
  README.md
```
---

## Core rules

- **NEVER put API keys, tokens, passwords, or other secrets into code, tests, fixtures, docs, or any file that is not covered by `.gitignore`.**
  Read them from the environment or from an ignored config file (`config.yaml`, `.env`, `.mcp.json` are already ignored). Commit `config.example.yaml` with placeholder values instead.
- **NEVER delete or destroy data or files on the box unless explicitly permitted.**
- Follow clean code policy.
- Keep code simple. Prefer removing complexity over adding new abstraction.
- Make the smallest complete change that fully solves the task.
- Match existing project conventions, naming, formatting, logging, and test style.
- Reuse existing helpers and patterns before creating new ones.
- Do not leave placeholder code, commented-out code, or speculative scaffolding.
- Never pin a cron job to an LLM provider; inherit whatever model the profile resolves at run time.

## Code hygiene

- Remove dead code paths when observed in touched areas.
- Remove unused or irrelevant variables, constants, functions, comments, and tests.
- Remove trivial tests that only restate framework behavior or add no meaningful coverage.
- Avoid broad catches, silent failures, and hidden fallbacks.
- Keep methods focused, names explicit, and control flow easy to follow.
- Keep comments short and to the point. Avoid repeating what the code already expresses. Use comments to explain *why* something is done, not *what* is done.

---
