# Shared computer controls

Dream exposes the same four tools through its normal registry for SDK, CLI bridge
and compatible HTTP models. The host needs Playwright's installed Chromium for
browser targets; desktop targets require an X11 session, `xdotool` and `xwininfo`. Window
listing also requires `wmctrl`. Missing dependencies are reported; these tools do
not install packages, share existing browser logins or control Wayland.

## Observe and act

1. `computer_observe()` reports capabilities and targets owned by this session.
   `computer_observe(list_windows=true)` explicitly lists titled X11 windows.
2. `computer_open(kind="browser", url="https://example.com")` opens a fresh,
   controlled Chromium context. It is separate from native Dream Browser and
   Studio. Or use `kind="desktop", window_id="..."` to attach the selected window;
   attaching does not focus it.
3. Read the returned state, screenshot and `observation_id`. Browser controls have
   `element_id` values. When `next_element_offset` is present, use it as
   `element_offset` in another observation to read more controls. Each page issues
   a fresh observation ID and replaces the previous one.
4. `computer_action` requires the target ID, current observation ID and one action.
   Browser `type` requires an observed element ID; typing replaces that field.
   Browser `click` accepts either that ID or integer x/y in viewport CSS pixels.
   Desktop `click` uses unscaled window-relative integer x/y; typing inserts
   into the current focus. Both accept 1–4000 text characters without NUL.
   Desktop typing uses 12 ms keystroke pacing in chunks of at most 128 characters,
   checking window focus, title and client geometry between chunks. It does not
   select a field, clear existing content or interpret arithmetic. `key` takes a named key/chord. Browser `scroll` uses
   dx/dy; desktop supports only dy, approximated as wheel steps inside the window.
   Desktop `focus` explicitly activates the selected window.
   Both targets accept `drag` with exactly two ordered `{x,y}` points or `stroke`
   with 2–128 points. Each uses the left button, held from first to last point.
   Optional `duration_ms=1–2000` requests paced movement with interpolation, or
   a stationary hold when all points coincide. Omit it to keep untimed dispatch.
   Supplied corners are retained. The internal plan uses up to 256 points, with
   scheduled gaps at most 20 ms and rounded steps within four pixels; requests
   requiring more subdivisions are rejected before input or token consumption.
   Shorten the path if needed. Actual timing includes backend/check overhead:
   `action_result` reports requested duration and elapsed movement/hold time
   measured after press acknowledgment and before release. This is not an exact
   duration guarantee. Drawing retains browser 8 s/native 15 s dispatch limits.
   All points must be inside the observed viewport/window; coordinates are never
   clipped. One complete stroke consumes one observation. Undo uses the app
   shortcut, such as `key="Control+z"` in a browser, then inspect the result.
5. Check the returned state. A successful dispatch does not prove that the requested
   save, navigation or task succeeded. If input was dispatched but the subsequent
   observation failed, `action_result.action_dispatched` is true and
   `observation_error` contains the error. Task success remains `unverified`. There
   is no usable after-observation ID or screenshot in that partial result. This
   can happen when Save As closes the selected dialog before capture. Do not send
   the same action again: observe the target and verify the saved item or changed
   state first. If the window closed, explicitly list and attach the intended
   replacement window before proceeding. Dream does not choose one automatically.
   Dispatch errors and cancellation remain uncertain; partial input may persist.
   Actions are not automatically replayed.
6. `computer_close` closes the owned browser context or detaches desktop control.
   It does not close the desktop application. Engine shutdown closes its controller.

Only pass fields used by that target/action. A browser key action does not use an
element ID, and a desktop type action does not use coordinates; such extra fields
are refused rather than silently ignored.

## Evidence and permissions

Observations briefly sample state and pixels to avoid capturing unfinished redraws
or delayed toolbar help. `readiness.status` reports `sampled_quiet`, `unsettled`,
or `pixels_unavailable`. The check samples at roughly 100 ms intervals, allows at
least 500 ms and 250 ms of unchanged samples, and stops waiting after 3 s. Browser
observations first allow two animation frames. If no quiet result or animation
callback arrives, the ordinary fresh coherent capture still runs and is labeled
unsettled. Read-only screenshots remain available for animated interfaces.
This timing describes recent samples, not future stability; later timers,
animations, edits and focus changes can still invalidate the action reference.
The same state and pixel checks apply to actions regardless of readiness.

Browser keys include letters/digits, navigation/editing keys, Space, F1–F12,
KeyA–KeyZ, Digit0–Digit9 and supported Numpad names. Common aliases are
Ctrl/CTRL→Control, Cmd/Command→Meta, Option→Alt, Esc→Escape, Return→Enter and
Del→Delete. The complete chord is validated before input dispatch; invalid names
and repeated equivalent chord modifiers do not consume a fresh observation.
The adapter releases attempted browser keys after a dispatch failure or
cancellation, and reports release failures. A disconnected browser can prevent
cleanup. Native key names continue to follow xdotool's contract. Native typing retains
the 5 s per-command bound; its overall dispatch deadline allows 15 s plus 12 ms
per character and 1 s per chunk, at most 95 s for 4000 characters. This is an
action-specific deadline. Stop waits for only the current bounded typing chunk
to finish so xdotool can release its generated keys and restore cleared modifiers;
no later chunk starts. A command or X11 failure can prevent that cleanup and is
reported. Partial text may remain after failure, Stop or a changed target. Observe
before retrying; the adapter does not replay or roll back typed text.

Observations are single-use for actions and expire after five minutes. Browser
checks include observed DOM controls, effective link/form destinations, focus,
viewport and navigation. References retain the actual observed nodes, so replacing
a node invalidates its action even if its visible text is unchanged. These checks
reduce known stale-state errors; they are not an atomic lock on a changing website.
Coordinate clicks and drawing also compare a fresh screenshot with the observed
pixels before dispatch. Animation or a blinking caret can require a fresh
observation. Before pressing, browser coordinates retain the actual hit node
and refuse hover-triggered replacement, overlays or changed actionable controls.
Non-actionable cursor status text can change during the initial hover. Browser
strokes stop on detected viewport, scroll or navigation changes. Native strokes recheck window focus, title and geometry between points.
Paced actions check the same target state before and after each wait and before
moving, including during a stationary hold. Supplied duplicate intermediate points
add no dwell; only an entirely stationary path holds in place for the duration.
Time-dependent tools can deposit more paint with slower input, but application
behavior is unchanged: JS Paint Airbrush still produces opaque stippled dots.
The adapter attempts button release even after a partial failure or cancellation;
a disconnected browser or unavailable X11 server can prevent release. Never
assume a failed stroke left the drawing unchanged.

Desktop checks cover window title, focus and geometry. A screenshot is an on-screen
crop at the selected window's client bounds. The root origin comes from
`xwininfo` absolute coordinates, keeping crop pixels aligned with window-relative
input even when the window manager adds decorations. Missing or malformed geometry
is refused; there is no guessed origin fallback. Overlays can appear in that crop. Inspect the
pixels and intended target before acting. Focus/geometry checks cannot prove that
every visual change or concurrent user action has been detected. Native X11 input
still needs daily-use qualification; automated regression checks use fake I/O. An isolated X11 Blender fixture also
verified repeated-digit typing and decorated-window crop alignment; this does not
establish daily-use or model task performance.

Screenshots have immutable observation-specific paths under private screenshot
storage. Pixels are attached only when the session reports working image input.
Without it, browser DOM evidence remains available and appearance stays unverified.
Do not put screenshots or other private run evidence in a source release.

Observations use the existing read permission class. Opening, acting and closing
use ordinary mutating-tool permissions, including in Auto; a shell sandbox does
not contain external websites or desktop applications. No blanket desktop grant
or permission bypass is added. Tool actions return observation references and a
before-state hash, not an automatically recorded teaching demonstration.

## Current limits

- Main-document browser controls only, up to 500 visible-first entries, returned
  in bounded pages. Closed shadow roots and embedded frames need another route.
- Downloads are disabled; new popup windows are closed and JavaScript dialogs
  dismissed. A task requiring those interactions needs another available route.
- Browser tabs are session-owned and do not inherit the user's native browser
  cookies or unsaved Studio state. At most four targets may be attached at once.
- Drawing uses straight segments between supplied points, without pressure,
  arbitrary scripts, right-button or modifier-held drags. File chooser control,
  native accessibility trees and Wayland remain unsupported. Blender scripts
  remain a useful route.
- Real Chromium fixture tests establish adapter behavior. They do not establish
  Fable/DeepSeek/Claude computer-use performance or native owner acceptance.

[Teaching skills](teaching-skills.md)
