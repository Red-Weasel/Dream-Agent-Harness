# Document formats and limits

Use `read_file` for extracted content, not a guessed binary-to-text conversion.
Its `offset` and `limit` page characters, default 0 and 12000, maximum limit 50000.
A truncation marker means there is more content, not that the document ends there.

- PDF: `page_start` is one-based; `page_count` defaults to 10 and is capped at 50.
  Example: `{"path":"report.pdf","page_start":11,"page_count":10}`.
  Poppler `pdftotext` and `pdfinfo` are optional installed prerequisites. Image-only
  scans need OCR; extraction does not perform OCR. Password-protected PDFs require
  an accessible copy. Say which case prevents reading.
- DOCX: extracted paragraphs and table cells describe current text, not tracked
  change history or page geometry. Preserve ZIP/XML parts and relationships when
  editing; do not rebuild a complex document from plain extraction.
- PPTX: extracted slide text and speaker notes allow a content check. Charts,
  positioning, images and animation still need rendering/inspection.
- Legacy .doc/.xls/.ppt or encrypted Office files: obtain an unlocked modern-format
  copy or use an already installed converter with the user's authorized scope.
- HTML/SVG/XML return source, not rendered appearance. Preview HTML or inspect a
  rendered image when the claim concerns layout.
- ZIP/TAR return an inventory only. Inspect member names and extraction destination
  before extracting; a listing does not mean archive contents were read.

For new Office output, check available format libraries through `run_bash` first.
Dream already uses python-pptx for PPTX exports; availability of python-docx,
openpyxl or LibreOffice must be checked. Use their real APIs when installed,
preserve source files and create a separate output. If the necessary editor is
unavailable, explain the limitation and provide an agreed supported format rather
than disguising text as an Office file or claiming conversion happened.
