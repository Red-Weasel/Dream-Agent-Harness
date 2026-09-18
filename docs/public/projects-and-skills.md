# Projects, documents, memory, and skills

Projects and Skills are workspace pages. Open either from the sidebar or the
workspace navigation. Moving between pages preserves unsaved editor drafts.

Projects and Skills have **Show** filters and **Sort** controls. Project filters
include the current workspace or projects with conversations; skill filters include
enabled, disabled and private versions. Search combines with the selected filter.
Use Arrow keys, Home/End and Enter to browse and open entries without a mouse.
Filtering does not discard an open editor draft.

**Context → Workspace spacing** selects comfortable or compact density. The artwork
banner and quiet-mode controls also persist across reloads. Dream's primary
destination navigation replaces the duplicate top row; narrow windows put New chat,
Design and Checkpoints under **Tools**. Classic presentation keeps its original routes.

## Return to a project

1. Open **Projects → New project**. Name it, choose its existing workspace folder,
   and optionally add project instructions. Saving the current workspace links
   the current conversation to that project.
2. Select a saved project to see its conversations, documents, memory, files,
   and instructions.
3. Choose **New chat in project**, or open an archived conversation and choose
   **Continue conversation**. Dream requires an idle session and an empty chat
   draft before switching. Pending work is never redirected to another folder.

Continuing restores a bounded saved summary and recent exchanges into a **new
agent session**. It does not reopen a provider's exact internal context or replay
old tool calls. It retains the chosen provider/model and reported effort, resets
session permission grants, and clears the previous Studio preview. Opening alone
does not send a generation request or run memory consolidation.

Recovery reserves separate space for recent user requests and assistant replies,
so a long assistant/tool sequence cannot consume the entire user-context allowance.
Large excerpts retain labelled beginning/end portions and source turn IDs. The
saved conversation remains the available record; recovery context is bounded.

New ordinary chat turns save start and terminal outcome records. Projects shows
the saved outcome and includes it in restored context and handoff drafts. Error,
interruption and incomplete results call for inspecting existing effects before
explicit continuation. A start without a terminal record, malformed/stale records,
and older conversations remain UNKNOWN; this does not prove a worker is still
running or stopped. Protocol completion does not verify the deliverable. Partial
reply fragments may be bounded or absent after a hard process crash.

Older conversations lack a reliable workspace field. Use **Link past conversation**
to explicitly assign an unassigned conversation to the correct project. Dream does
not guess ownership from its text. Linking makes its existing summary and linked
memory records visible; it does not rewrite the archived conversation.

## Documents and project memory

**Documents** holds editable private Markdown references and memory notes. Create a
note for decisions, constraints, lessons, or a compact handoff. Check **Include in
project context** for notes Dream should receive in project messages. Selected
notes have a combined 6,000-character limit including headings; oversized selections
are rejected rather than silently clipped. Other notes remain available in the page.
Project instructions are supplied separately. Existing workspace pins and file
search remain in **Files & context**, available when that workspace is open.

**Memory** displays saved session summaries and memory records linked through their
latest source session. A merged legacy memory may contain material from other
sessions; that provenance limitation is shown. **Copy to project memory** creates
an editable note while preserving the original record. Selecting that copy for
context is a separate explicit choice.

Conversations are saved during work. End-of-session consolidation is a separate
optional model pass; this page does not make it instant or automatically turn it
on. Existing global memory and recall continue to work. This change adds scoped
project notes and views; it does not migrate or repartition the legacy memory store
or add a new embedding model. User-owned notes are authoritative Markdown. No
background learning policy is enabled by opening Projects.

To prepare a handoff, open a project's conversation and choose **Draft project
handoff**. Dream opens an unsaved Markdown draft in Documents with bounded saved
summary/request/assistant excerpts and source turn IDs. No model is called.
Assistant reports are not independent verification; confirm the current goal,
artifact paths, actual checks, known defects and next action before saving.
Excerpts are explicitly partial, and missing facts stay UNKNOWN.

Choose **Save document** to persist the reviewed handoff immediately. Context
inclusion starts off; select **Include in project context** only if it should guide
future turns. **Documents → New handoff** provides a blank template. Drafts are
protected against late capture responses and project/session navigation; browser
reload still requires saving first. Keep the full conversation in Conversations
and reusable lessons separate from project state.

## Saving memories and closing Dream

The `remember`, `memory_write`, `memory_append` and `memory_str_replace` tools
save the authoritative Markdown file and keyword-search row
without calling the optional embedding model. Vector indexing is deferred to the
existing embedding backfill lifecycle. Edits clear obsolete vectors immediately;
results from an older in-flight embedding cannot overwrite newer content. Saved
memories remain available through keyword search while vector indexing is pending.

For different wording, `recall` accepts `alternative_queries`: up to three
caller-written rephrasings in the same call. For example, a query for food
restrictions can include `"dietary constraints"`. Alternative-only hits identify
the query that found them; duplicates appear once. The top direct primary hit
keeps its position, then alternative hits precede broader primary matches.
Dream does not generate rephrasings or add a rewriting model. Alternatives use
the existing retrieval configuration, including optional semantic search and
reranking when enabled. With no alternatives, valid calls use the existing
retrieval path. If nothing matches,
any recent-memory fallback is explicitly labelled as such.

Recall queries must contain 1–256 characters after trimming, alternatives have
the same bound, and `limit` must be an integer from 1 through 20. Blank queries,
unknown fields and malformed limits return an error. Browsing the Memory page
remains available; a blank recall query is no longer a browsing shortcut.

A failed file replacement preserves the previous file and rolls back its database
update. If the file was saved but the search index failed, the response identifies
that partial result and its recovery path. Markdown and MEMORY.md use atomic
replacement with flushed file contents and directory synchronization. If replacement
occurred but directory durability could not be confirmed, the tool reports that
partial result and keeps keyword data aligned when its database commit succeeds.
Read the current file before retrying; versioned writes reject a stale revision.
Directory synchronization requires filesystem support and permission. These checks
do not prove survival of every storage failure.

Declining end-of-session consolidation retains the saved conversation. Accepting
it requests an optional model-generated summary and memories, which can still
take minutes. The close prompt now distinguishes these operations. This change
does not make all model consolidation instantaneous or start an indexing worker
in the background after Dream has closed.

The project catalog lives under Dream's data directory as `project-library.json`.
Private notes live in `project-documents/<project-id>/*.md`. Each file starts with a
small metadata comment identifying the note and its context selection. Session and
consolidation data remain in the existing database. Keep all of these out of source
releases and back them up privately.

## Edit or build a skill

Open **Skills**, choose an entry, and read its full `SKILL.md`. Edit the Markdown and
save it, or choose **New skill** and fill in the supplied template. A skill needs YAML
frontmatter with a matching name and nonempty description. New names use lowercase
letters, digits, and hyphens. **Use in chat** prepares a message; it does not send it.

Edits to bundled or imported skills create a private override under the data
directory's `skills/` folder. Originals remain untouched. Safe supporting files
are copied with the package; symlinks, special files, hidden entries, oversized
bundles, and escaping or missing Markdown references are refused with an error.
The editor handles `SKILL.md`; supporting-file editing is outside this page.

Private skills take precedence in discovery, including when custom skill roots
are configured. Saving refreshes the catalog. It does not enable a disabled parent
plugin, approve a custom tool, or execute a support script. The next applicable
skill load uses the saved version; a running model turn is not rewritten.

Task routing ignores conventional quoted instruction examples, fenced blocks
and blockquotes, and avoids deriving implicit workflows from negated actions.
Explicit skill names and quoted filenames remain usable. This is a bounded
routing heuristic, not full language understanding or a permission boundary.
If the admitted workflow is shortened, Dream instructs the agent to open its
complete instructions with `skill_open` before following it; loading an excerpt
does not establish that required verification steps were retained or performed.

Saves compare the content revision you opened against the current file. If another
editor changed it, Dream rejects the stale save and keeps your draft. Discard changes
and reopen the entry to load the newer version. Leaving the page preserves the draft;
closing/reloading the browser may discard it after the browser's unsaved-change warning.

## Tool feed labels

**Inspect arguments · N request characters** counts the tool arguments, such as a
file path in JSON. It is not a file-read limit. **Inspect result** counts returned
text before the feed's display truncation. Repeated read calls are distinct events;
the argument-length label does not diagnose why a model repeated them.

[Runtime guide](runtime.md) · [Data and privacy](privacy.md)
