# HTML, deck and export routes

## Slide deck

For a new HTML deck, `copy_starter_component(kind="deck_stage.js")` provides
`<deck-stage>` with scaling/navigation. Put each slide in a direct `<section>` child;
use a 1920x1080 design canvas. Start from the brief, choose a small set of reusable
layouts, and write real slide content. Do not block on routine style choices.

Use `show_html`, `get_webview_logs` and `multi_screenshot` to check representative
slides, especially dense ones. Navigate with `goToSlide(0)`, `goToSlide(1)`, etc.
Inspect captured images with `see` when vision is supported. Optional speaker notes
are a string array in `<script type="application/json" id="speaker-notes">`.

## PDF

`open_for_print(project_relative_file_path, width?, height?, output_path?)` prints
HTML to PDF. For a deck pass width=1920 and height=1080; for a document omit them
and use its `@page` styling. Use `landscape=true` for a wide document when needed.
Inspect print layout and read back the PDF, not just the original HTML.

## PPTX

`gen_pptx` takes path, canvas width/height, slides and mode. Each slide supplies a
root `selector` and `showJs` that navigates before capture. For the deck starter,
use `resetTransformSelector="deck-stage"`. `screenshots` produces image slides;
`editable` creates supported native text/shapes. Do not promise perfect editable
fidelity for complex effects. Read its validation flags: duplicate adjacent slides
usually mean navigation failed; size mismatch means a selector/scale problem.
Fix the inputs and regenerate, then inspect the output.

## One offline HTML file

`super_inline_html(input_path, output_path)` bundles local assets. Include
`<template id="__bundler_thumbnail">` containing a small SVG thumbnail. Keep
assets local and relative. The tool reports remote or outside-folder references
that remain external; do not call that output fully offline until resolved.
Preview the final bundle and check its console/interactions before delivery.
