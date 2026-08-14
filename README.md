# my_claw

A skill-driven agent plugin for Dify with **progressive disclosure**,
**cross-session memory** and **hot-swappable skill packs**.

my_claw is a modernized, re-architected fork of
[mini_claw](https://github.com/lfenghx/mini_claw) v1.2.0 (Apache-2.0),
re-released with the original author's blessing. See [NOTICE](NOTICE) for
provenance.

## Highlights

- **Skill packs with progressive disclosure** — drop a zip containing a
  `SKILL.md` manifest plus scripts; the agent loads instructions only when a
  task matches, so context stays small. Strictly typed manifests (pydantic)
  make skill eligibility deterministic instead of heuristic.
- **Whole-round streaming, dual-channel reasoning** — one model call is an
  indivisible round: intermediate tool-call rounds publish nothing, the final
  round publishes complete, tag-balanced text. Reasoning is consumed from the
  dedicated `reasoning_content` field when the platform provides it and falls
  back to `<think>` tag parsing otherwise. No more answers swallowed by an
  unclosed thinking region.
- **Cross-session memory and persona** — a four-document persona model
  (identity, user profile, soul rules, managed memory) plus daily digests,
  all modularized and unit-tested.
- **Batteries for real work** — file generation skills (docx/xlsx/svg/ics),
  group messaging (Feishu/Lark webhook), each as an isolated, zero-dependency
  skill pack.
- **Security-first execution** — command allow-lists, path-escape guards,
  SSRF-protected fetching, zip-slip protection, and an approval workflow for
  sensitive operations.
- **Platform-agnostic kernel** — the agent core has zero SDK imports and is
  covered by unit tests; Dify is an adapter, so other hosts are possible.

## Source & contact

- Repository: <https://github.com/qiqikuaidianpao/my_claw>
- Contact: open a GitHub issue in the repository above.

## Install

### From marketplace

Search for **my_claw** in the Dify plugin marketplace (coming soon).

### From local package

1. Download or build `my_claw.difypkg` (see below).
2. In Dify: **Plugins -> Install plugin -> Local package file**, then drop the
   `.difypkg` file.

## Quick start

1. Create a Chatflow (or open an existing one), add a tool node and pick
   **my_claw**.
2. Configure the model you want the agent to use (any model installed in your
   workspace).
3. Wire the user query (and optional file list) into the tool node, and the
   tool's `text` / `files` outputs into a reply node.
4. Open the app and try:

   ```text
   list skills
   Calculate a lease: principal 5,000,000 CNY, 6% annual, 36 equal installments
   Send the result to the project group
   ```

### Manage skill packs

Use the bundled **skill manager** tool in the same chatflow:

- `list skills` — show installed packs and their eligibility
- upload a `.zip` + `install skill` — hot-install a new pack
- `remove skill 2` — uninstall by index

A skill pack is a directory (zipped) with a `SKILL.md` front-matter manifest:

```yaml
---
name: my-skill
description: What this skill does
read-when: When the agent should load this skill
metadata:
  requires:
    bins: [python3]
---
# Instructions for the agent ...
```

## Development

```bash
pip install -r requirements.txt pytest
pytest tests/ -q          # kernel unit tests (no Dify needed)
python scripts/package.py # build my_claw.difypkg
```

The layout separates a pure kernel from the platform adapter:

```
core/           platform-agnostic agent kernel (no SDK imports)
adapters/dify/  Dify plugin glue: LLM client, storage, message emitter
provider/ tools/  plugin manifests (thin)
tests/          unit tests for kernel and adapters
```

## License

Apache-2.0. This project derives from mini_claw by lfenghx (Apache-2.0);
attribution and per-module provenance are recorded in [NOTICE](NOTICE).
