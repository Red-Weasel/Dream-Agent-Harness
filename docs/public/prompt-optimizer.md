# Prompt Optimizer

Open **Prompt Optimizer** in the workspace's left navigation while using Chat.
Enter your rough prompt and, optionally, the result you want. Attach relevant
files and expand the optional details for context, constraints and verification.

Choose **Standard**, **Careful**, or **Compare alternatives**. These options add
instructions to the draft. They do not change the model's actual reasoning effort;
use Dream's model controls for that setting. Target style is also writing guidance,
not a model switch. Auto uses the selected model's context.

- **Optimize prompt** makes one isolated request to the selected provider/model.
  Dream must be idle. It can propose up to three questions whose answers would
  materially change the result. Answer them and refine when useful.
- **Quick structure** formats the entered details without a model call. It works
  while other work is running and does not inspect attachment contents.

The draft uses the relevant parts of this structure:

```text
GOAL
CONTEXT & EVIDENCE
CONSTRAINTS
AUTONOMY
OUTPUT
VERIFICATION
```

Review and edit the result. **Use in Chat** puts it in the composer for you to
send. If the composer already has text, use the explicit append action. Copy is
also available. Preparing or transferring a prompt never starts its task.

Only selected, registered attachments are included. Model optimization can read
bounded excerpts of supported text files: up to 4,000 characters per file and
16,000 combined, with at most eight files. Images, PDFs and other unsupported
formats are represented by filename/path metadata, not claimed visual or document
inspection. The files themselves remain available for transfer to Chat. Enable
the source-only option when factual claims must come exclusively from supplied
evidence; missing facts should be labelled UNKNOWN.

The panel retains its draft in the current page, separate from Chat. Switching
session or workspace makes an old draft inactive until you explicitly start a
draft in the new context. A late response cannot overwrite more recent edits.
Reloading the page can lose the optimizer draft; copy important drafts first.
Optimizer drafts are not automatically committed to memory. Attachments use
Dream's existing private workspace upload storage and normal retention behavior.

Model failure leaves the draft available and reports an error. There is no
substitute model or automatic switch to Quick structure; the shared provider
transport can retry transient connection failures. Stop cancels the isolated request. A generated
prompt still needs review: correct JSON and successful transport do not prove the
model preserved every requirement or improved the eventual result.

See the [prompting research note](../research/prompt-optimizer-guidance.md) for
the primary sources behind the short target-style guidance.
