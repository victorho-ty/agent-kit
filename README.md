# Agent-kit

Building blocks, skills, and utilities for modern AI agents.

Agent **profiles**; each bundle inside with deterministic Python package plus the skill and data that drive it.


## Approach

Code does the deterministic work: data ETL, maths, dedupe, charting, and state tracking.

Model does the judgement work: deciding what is worth saying,
writing it for the reader, answering questions about it, and the calls the tools
deliberately refuse to guess at — an ambiguous listing, a peer set, a verdict a
regex should not pretend to have.

Everything the model says about the data comes from a payload the tools returned.
Model never computes a number.


## Optimising token usage

- **Schedule code, not prompts.** Detection runs on cron as a plain script and
  costs nothing. The model is woken only when a ledger says there is something to
  report (e.g. quarterly source can be checked daily for free)
- **Hand the model finished strings** where the formatting is fixed — that is
  what stops a forty-ticker watchlist costing forty paragraphs.
- **Keep SKILL.md small.** Command surface and domain detail live in
  `references/`, opened only when a task needs them.


### The never-firing cron job trick

A script cannot start an agent session directly; it can only trigger an existing
cron job. So an on-demand agent job is registered with a schedule that never
comes round:

```
59 23 29 2 *        # 23:59 on 29 February
```
The gate script fires it when, and only when, there is something to report, passing payload to model.
