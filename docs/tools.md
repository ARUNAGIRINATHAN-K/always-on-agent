# Tool Authoring & Extensibility Guide

Tools in the Always-On Planning Agent are strongly typed, bounded to safe directories, and guaranteed to be idempotent.

---

## The Three Iron Rules for Tools

Every tool added to [agent/tools.py](file:///b:/My%20Project/always-on-agent/agent/tools.py) must adhere to these rules:

1. **Declared & Checked Parameters**:
   Every argument must be explicitly declared in `params`. The planner's proposed arguments are validated before anything runs. Missing or unknown arguments fail immediately without reaching your function.
2. **Confined to Safe Boundaries**:
   Tools must never accept arbitrary file system paths. Validate filenames against strict allowlists or regex patterns (e.g. `^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(md|txt)$`).
3. **Idempotence**:
   Because tool steps can be retried after timeouts or network drops, executing the same tool twice with the same arguments must leave the system in the identical state.

---

## Step-by-Step: Adding a New Tool

### Step 1: Write the Tool Function
Add your function to [agent/tools.py](file:///b:/My%20Project/always-on-agent/agent/tools.py). The first argument must always be `ctx: ToolContext`.

```python
from .tools import ToolContext, ToolInputError

def search_notes(ctx: ToolContext, query: str) -> str:
    """Search for text across notes in the data directory."""
    query = query.strip()
    if not query:
        raise ToolInputError("query must not be empty")

    matches = []
    if ctx.notes_dir.exists():
        for note_path in ctx.notes_dir.glob("*.md"):
            content = note_path.read_text(encoding="utf-8")
            if query.lower() in content.lower():
                matches.append(note_path.name)

    if not matches:
        return f"No notes matched query '{query}'."
    return f"Found '{query}' in: {', '.join(matches)}"
```

### Step 2: Register in `TOOLS`
Register the tool in the `TOOLS` dictionary in [agent/tools.py](file:///b:/My%20Project/always-on-agent/agent/tools.py):

```python
TOOLS["search_notes"] = ToolSpec(
    name="search_notes",
    description="Search for keywords or phrases across existing markdown notes.",
    params={"query": "search query string"},
    fn=search_notes,
    requires_approval=False, # Set True if tool alters state or deletes data
)
```

The planner automatically learns about your new tool through `describe_tools()` on its very next run.

### Step 3: Add Unit Tests
Add tests in [tests/test_tools.py](file:///b:/My%20Project/always-on-agent/tests/test_tools.py) verifying input validation, error handling, and output:

```python
def test_search_notes_finds_matching_file(ctx):
    run_tool("create_note", {"filename": "test.md", "content": "Machine Learning"}, ctx)
    output = run_tool("search_notes", {"query": "Learning"}, ctx)
    assert "test.md" in output
```

---

## Built-In Tool Specifications

| Tool Name | Parameters | Side Effect? | Requires Approval? | Description |
|---|---|:---:|:---:|---|
| `create_note` | `filename`, `content` | Yes | No | Writes or overwrites a markdown note atomically via temporary swap. |
| `save_task` | `title`, `status` | Yes | No | Inserts or updates a task row by hashed title. |
| `delete_note` | `filename` | Yes | **Yes** | Deletes a note file. Fails fast if triggered in unattended cron mode. |
