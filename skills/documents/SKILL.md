---
name: documents
description: Read, create, convert or edit PDF, DOCX and PPTX documents, slide decks and printable files.
---

# Documents

1. Confirm the source and requested output format. Use `read_file` for document text before making claims: PDF supports `page_start`/`page_count`; long text supports `offset`/`limit`. Extracted text does not prove the layout is correct.
2. Preserve the original and edit a workspace copy. `write_file` writes text, not Office binaries. Use a format-aware installed library through `run_bash` for Office generation or edits; check its availability before relying on it. Never rename plain text to .docx/.pptx.
3. For HTML-based decks, PDF printing, PPTX export or a single offline HTML, read [exports](references/exports.md). For reading limitations and format preservation, read [formats](references/formats.md). Fetch only the relevant reference using `skill_file`.
4. Check the saved output by reopening it with `read_file` and inspecting rendered pages when layout matters. Verify page/slide count, content, tables and clipping. Distinguish editable shapes/text from screenshot slides.
5. Deliver the actual output path with `show_to_user` where supported. State missing OCR, rendering or format fidelity honestly. If editing a Library item, replace the same identity instead of creating a duplicate.

Read [worked examples](references/examples.md) with `skill_file` when a concrete tool sequence would help.
