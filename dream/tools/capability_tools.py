"""The experiment workspace used by illuminati-handshake."""
import json
from claude_agent_sdk import tool
from .context import ctx, ok, err, in_thread
from .. import capability_lab


@tool("capability_lab_create", "Create an isolated capability experiment with explicit acceptance criteria. Choose tool for ordinary engineering, rl only for repeated decisions with a measurable reward. RL scaffolds a bounded CPU action-policy environment, not LLM training. Nothing is installed or enabled.",
      {"type": "object", "properties": {"name": {"type": "string"}, "goal": {"type": "string"},
          "criteria": {"type": "array", "items": {"type": "string"}},
          "method": {"type": "string", "enum": ["tool", "rl"]}}, "required": ["name", "goal", "criteria"]})
async def capability_lab_create(args):
    try:
        result = await in_thread(capability_lab.create, ctx().workspace, args["name"], args["goal"],
                                 args["criteria"], method=args.get("method", "tool"))
        return ok(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        return err(str(exc))


@tool("capability_lab_inspect", "Read an experiment's acceptance criteria, file hashes and reported evaluation. A reward/report is not independent verification or permission to promote code.",
      {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]})
async def capability_lab_inspect(args):
    try:
        return ok(json.dumps(await in_thread(capability_lab.inspect, ctx().workspace, args["id"]), ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        return err(str(exc))


CAPABILITY_TOOLS = [capability_lab_create, capability_lab_inspect]
