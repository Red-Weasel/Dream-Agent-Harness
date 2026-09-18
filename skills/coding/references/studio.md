# Studio interfaces

Use the project's existing stack for an existing application. For a new standalone
preview, `copy_starter_component` can supply `design_canvas.jsx` for layout options,
`ios_frame.jsx`, `android_frame.jsx`, `macos_window.jsx` or `browser_window.jsx` for
framed prototypes. Its response gives the local vendor files and script tags.
Dream's preview is network-restricted; do not assume CDN scripts can load.

Keep the requested interaction real: navigation, forms, loading, empty and failure
states. Reuse supplied brand values or establish a small consistent type/color/
spacing system. For alternatives, place labeled options on one canvas. Do not add
an interview, design-system project or extra controls unless the task benefits.

For the Babel JSX starters, components loaded by separate scripts must export with
`Object.assign(window, {ComponentName})`; give module-level variables unique names.
Use `show_html` and `get_webview_logs` to check startup, then `eval_js` to exercise
important transitions. `save_screenshot` captures a state; `see` inspects that image
only when the model supports vision. Inspect narrow/wide layouts and keyboard use.
Deliver through `done` or `show_to_user`; report unperformed visual checks.

## Optional live tweaks

Add tweaks only when requested or useful for a design choice. Keep valid JSON in
exactly one inline script block in the root HTML:

```javascript
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{"gap":16}/*EDITMODE-END*/;
```

Register a `message` listener for `__activate_edit_mode` / `__deactivate_edit_mode`
before posting `{type:'__edit_mode_available'}` to the parent. Toggle the controls
when these arrive. On an edit, apply it and post
`{type:'__edit_mode_set_keys', edits:{gap:20}}` to the parent. Studio persists these
keys for an opened file. Verify the resulting file and a reload. Fenced snippets
have no file to persist into. Do not add controls when the user asked for none.
