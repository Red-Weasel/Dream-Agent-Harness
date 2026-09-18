# Runtime data and publication privacy

Dream's code supports memory; a source release does not need anyone's actual
memories. Treat these as separate from reusable code:

| Local content | Why it stays private |
|---|---|
| `data/` | Databases, sessions, library files, and other runtime records |
| Root `memory/` | Facts, preferences, identity, instructions, and session summaries |
| `var/`, `.dream/`, `.remember/` | Logs, run state, media state, and observations |
| `.env*`, provider settings, `mcp.json` | Credentials, endpoints, and local integration configuration |
| Agent/editor settings and raw exports | Personal instructions, account state, and conversations |
| Backups, recordings, uploads, artifacts | Copies of runtime data or project deliverables |

`dream/memory/` is Python implementation code and belongs in the release. It is
not the root `memory/` directory containing user data. The `eval/` corpus and test
fixtures are synthetic development data, not an installed user's memory store.

The public source snapshot is selected from code, bundled assets, curated skills,
tests, and public documentation. Local project handoffs and private development
records are not included. Repository ignores and wheel exclusions provide additional
protection, but neither is a complete privacy audit.

Before publishing your own fork, inspect staged files, commit contents, and history.
Ignoring a path does not untrack it, and deleting it in a new commit leaves earlier
versions accessible. Do not commit runtime backups. If secrets were exposed,
rotate them; deleting a file is not credential revocation. Historical removal can
also require host support for cached views or pull-request references. Copies
already downloaded cannot be recalled by changing a repository.

## Data sent to models

The selected provider can receive prompts, recalled context, attachments, and tool
results required by a task. Hosted providers have their own retention and account
policies. Local inference and local storage do not make arbitrary extensions,
external tools, or websites private. Review integrations and their permissions.

Back up your own runtime data privately. Markdown memory files alone do not contain
all session, task, and library state; preserve the relevant databases as well.
