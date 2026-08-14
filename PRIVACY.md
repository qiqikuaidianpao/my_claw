# Privacy Policy for my_claw

_Last updated: 2026-08-14 (development pre-release)_

my_claw is a tool plugin for Dify. This page describes what data the plugin
handles when installed and used.

## What data my_claw processes

- **Conversation content you send to the agent tool node** (your query, uploaded
  files) is forwarded to the LLM configured in your Dify workspace, and may be
  written to the plugin storage attached to the app as session history and
  long-term memory notes (persona facts, daily digests).
- **Skill packages you install** are stored in the plugin storage and unpacked
  into the plugin runtime workspace when used.
- **Generated artifacts** (documents, sheets, charts) are produced in a
  per-session temporary workspace and delivered back as tool outputs.

## What my_claw does NOT do

- It does not send your data to any third-party service other than the LLM
  provider you configured in Dify.
- It does not include telemetry, analytics or crash reporting.
- It does not store credentials, API keys or secrets.

## Storage and retention

- Long-term memory files are kept in plugin storage until you delete them
  (persona reset command) or the workspace is removed.
- Session workspaces are temporary and rotated automatically (a small number
  of recent sessions are kept).

## Your control

You can reset persona/memory at any time through the agent conversation, and
workspace administrators can inspect or purge plugin storage from Dify.

## Contact

Open an issue in the plugin's GitHub repository for any privacy question.
