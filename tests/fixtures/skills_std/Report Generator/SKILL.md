---
name: Report Generator
description: Build a formatted business report from scattered bullet points and raw notes.
metadata:
  openclaw:
    os: [linux, win32]
    requires:
      bins: [python3]
---

# Report Generator

Takes the user's rough notes and produces a structured report:

- Title and date header
- Sections grouped by topic
- Action items table at the end

The output format follows the company report template in `template.md`.
