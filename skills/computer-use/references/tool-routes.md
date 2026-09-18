# Tool routes and state checks

These names describe Dream's source contract. The active session's tool inventory
and returned schema determine availability and arguments; skill prose grants no
new controls. In another harness, map the capabilities to its listed tools.

| Target | Available Dream route | Boundary |
| --- | --- | --- |
| Workspace HTML artifact, private inspection | `show_html`, then `eval_js` for DOM/state and `save_screenshot` for captures | Hidden preview has no network. It is not the user's open browser. |
| Artifact the user currently sees in Studio | `eval_js_user_view`; `screenshot_user_view` when private preview cannot reproduce its state | Studio must be connected with an artifact open. This does not inspect native app chrome or other windows. |
| Read a live URL | `browse` with optional `screenshot` and `wait_selector` | Returns readable text and possibly a screenshot path. Its schema supplies no click/type controls. It does not establish access to another browser's signed-in session. |
| Inspect saved pixels | `see(path)` when listed and image input works | A saved path or image metadata is not visual inspection. Disabled or failing vision leaves visual checks unverified. |
| Control a live browser or native desktop | Listed `computer_open`, `computer_observe`, `computer_action`, or another connected controller | Discover the actual schema/capabilities. Studio DOM tools do not provide desktop control or browser login sessions. |
| Deliver a workspace HTML preview | `show_to_user(path)` for a draft; `done(path)` for final HTML | `show_html` alone is private. Check the delivery response; a queued presentation does not prove the user saw it. |

## Hidden and visible Studio state

For local HTML, call `show_html` with the real workspace file and query the current
DOM using `eval_js`. Inspect labels and element count before deriving a selector;
check the relevant resulting value, panel or status after the interaction. A DOM
`.click()` can exercise handlers but does not establish physical hit testing,
keyboard accessibility or native-dialog behavior.

`save_screenshot` requires `path` and a nonempty `steps` list, plus exactly one of
`save_path` or `in_memory_png_key`. For one saved capture, use `steps:[{}]` and a
workspace-relative `.png`, `.jpg` or `.jpeg` path. Inspect the returned path with
`see`. An in-memory key is not a disk path for `see`. `multi_screenshot` requires
JavaScript in every step. Capture only states useful for the task.

`screenshot_user_view` serializes the visible artifact's live DOM and form values
and renders that snapshot in the hidden preview. It does not preserve state held
only in JavaScript variables. It also replaces the hidden page: reload the intended
workspace HTML with `show_html` before continuing private interaction checks.
Use `eval_js_user_view` for the actual user state when that distinction matters.

## External interfaces

Dream's computer adapter uses explicit targets. `computer_open(kind="browser",
url=...)` opens a controlled browser; `kind="desktop", window_id=...` attaches a
specified X11 window without focusing it. `computer_observe()` reports capabilities
and owned targets; `list_windows=true` explicitly lists available windows. An
unavailable backend or unsupported desktop session cannot be repaired by guessing
coordinates. A controlled browser is separate from native Dream Browser and Studio,
with no implied shared cookies.

Use `computer_observe` to obtain a fresh observation and then `computer_action`
with that target and observation ID. Observation IDs are single-use, including
uncertain dispatch. Inspect the returned fresh state before another action, or
observe again when it is missing. Browser click accepts a returned `element_id`
or integer viewport `x`/`y`; browser type replaces the field at `element_id`.
Desktop type inserts at current focus. Desktop click uses unscaled window-relative
`x`/`y`; focus is a separate explicit action. Pixels
are available only for the focused desktop target. After a stale-state refusal,
refresh and locate the target again. Previous coordinates and element IDs are no
longer evidence. Dispatch completion alone is not task completion.

If `action_result.action_dispatched` is true but after-action observation is
missing, read `observation_error` and treat the application outcome as unverified. A
Save As dialog may have closed successfully before its screenshot. There is no
new action token in that partial result. Verify the saved file or current state
before retrying. If the original window is gone, explicitly list and select the
intended replacement; do not reuse its coordinates or switch to an arbitrary
window. A dispatch failure or cancellation can leave partial changes too.

For canvas work, `drag` takes exactly two `{x,y}` points and `stroke` takes
2–128, using the left button along the path. Map points against the current
screenshot and app zoom, not the full document dimensions. The controller does
not supply pressure, modifier-held mouse input or double-click parameters. Do not
invent those arguments. Read the active schema if a task requires finer controls.
When listed, optional `duration_ms` paces a drag/stroke or stationary hold; requested
duration excludes practical dispatch overhead. Inspect the actual mark. The tool
rejects overly long subdivision plans before input; use shorter strokes instead
of repeating the rejected request. This does not add pressure or color blending.

Returned `readiness.status` is advisory: `sampled_quiet` describes recent samples;
`unsettled` can still include fresh pixels on an animated page. It does not bypass
state/pixel checks or guarantee that the interface will stay unchanged. Inspect the
returned result before using its observation. Reobserve when a refusal identifies
changed evidence; do not replay the same stale token or claim the prior action
failed merely because its resulting screenshot arrived late.

An action may already return the fresh screenshot and observation ID needed for
the next action. Inspect that result before issuing another `computer_observe`.
Use its valid token when the target and state still match. Request a new capture
for missing evidence, delayed feedback, a changed view or a stale-state refusal;
do not add an identical refresh after every successful action. Identical pixels
alone do not make a refresh useless: it may restore a valid single-use token.

Browser chords accept canonical keys such as `Control+z`; common Ctrl/CTRL, Cmd,
Option, Esc, Return and Del aliases are documented by the adapter. Native keys
follow xdotool and still accept a single chord. Known invalid browser chords are
rejected before dispatch without consuming an observation; genuine dispatch or
cleanup failures can leave uncertain state. Follow the returned error category.

Page more controls using `element_offset=next_element_offset` when returned. Use
the newest page's observation and element IDs. Desktop pixels are an on-screen
crop and can include overlays; check the visible target, not just its window title.

`computer_close(target_id=...)` closes an owned browser or detaches desktop control;
it does not close the desktop application. These controls use normal permissions.
When image input is unavailable, browser DOM evidence remains usable, but a saved
screenshot path does not establish visual inspection or authorize guessed clicks.

With an available browser controller, verify the current tab/URL and document,
then obtain fresh DOM/accessibility evidence. Resolve duplicate labels using a
specific panel or row. Wait for a concrete state such as a loaded heading or saved
status. Reacquire elements after page changes instead of repeating stale IDs.

For native controls with screenshot input, verify the active window, scale and
focus before typing or using a shortcut. If the screenshot is resized by the
viewer, use the tool's documented coordinate mapping. An old frame cannot identify
a current modal button. After a drag, inspect the actual destination and result.

If an operation may have completed before an error, inspect the saved item or
status first. Match the document/item identity and expected value or revision,
not merely a generic "Saved" label or unchanged editor text. If it persisted,
continue to verification without another submission. If it did not persist,
reacquire the control and retry once the cause is understood. If persistence is
still unknown, retain the draft and report that uncertainty. If the exact target
remains unknown, obtain the missing state instead of sending guessed clicks.
