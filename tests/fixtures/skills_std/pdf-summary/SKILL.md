---
name: pdf-summary
description: Extract key points and action items from long PDF documents. Use when the user asks to summarize, digest, or pull highlights from a PDF.
license: MIT
allowed-tools: Read, Bash
metadata:
  version: 1.2.0
  author: community
---

# PDF Summary

Read the PDF the user attached, then produce:

1. **Summary** — 2-3 sentences capturing the core thesis
2. **Action items** — bullet list of concrete asks found in the document
3. **Key figures** — table of numbers/dates worth reporting

Rules:
- Only report what is actually in the document; never invent content.
- Mark unclear items as `[unclear]`.
