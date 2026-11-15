"""The LangGraph workflow: recall -> plan -> (approve?) -> execute, repeated per step.

Design notes that differ from the simple version in the article:

* The planner must return JSON. Every step is checked against the tool
  registry before anything runs, and the planner gets one correction attempt.
* Execution is one step per node visit. Each visit is checkpointed, so a run
  that crashes or pauses resumes at the next unfinished step and does not
  repeat completed work.
* The approval node does nothing except call `interrupt()`. LangGraph re-runs
  a node from its start on resume, so a node with side effects before its
  interrupt would repeat them. Keeping the interrupt alone avoids that.
* Tools get timeouts and retries with backoff. Invalid arguments are not retried.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .config import Settings
from .memory import LongTermMemory
from .tools import TOOLS, ToolContext, ToolInputError, describe_tools, validate_args

log = logging.getLogger(__name__)

APPROVE_WORDS = {"approve", "approved", "yes", "y", "true", "ok"}


class PlanError(ValueError):
    pass


class AgentState(TypedDict, total=False):
    goal: str
    user_id: str
    thread_id: str
    memories: dict[str, str]
    plan: list[dict[str, Any]]
    completed: list[str]          # step ids that have run, failed, or been skipped
    decisions: dict[str, str]     # step id -> "approved" | "rejected", for steps that need approval
    results: list[dict[str, Any]]
    status: str                   # planned | running | completed | completed_with_skips | failed
    error: str | None


# --------------------------------------------------------------------------- #
# Planning helpers
# --------------------------------------------------------------------------- #

def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise PlanError("planner reply contained no JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise PlanError(f"planner reply was not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise PlanError("planner reply must be a JSON object")
    return data


def parse_plan(text: str, max_steps: int) -> list[dict[str, Any]]:
    data = _extract_json(text)
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("planner returned no steps; the goal may be outside what the tools can do")
    if len(raw_steps) > max_steps:
        raise PlanError(f"plan has {len(raw_steps)} steps; the limit is {max_steps}")

    steps: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_steps, start=1):
        if not isinstance(raw, dict):
            raise PlanError(f"step {i} is not an object")
        tool = raw.get("tool")
        args = raw.get("args", {})
        try:
            validate_args(str(tool), args)
        except ToolInputError as exc:
            raise PlanError(f"step {i}: {exc}") from exc
        steps.append(
            {
                "id": f"s{i}",
                "tool": tool,
                "args": args,
                "description": str(raw.get("description", ""))[:300],
            }
        )
    return steps


def _build_planning_prompt(state: AgentState, settings: Settings) -> str:
    memories = state.get("memories") or {}
    memory_block = "\n".join(f"- {k}: {v}" for k, v in memories.items()) or "(none)"
    return f"""You are the planning module of a personal task agent.

Goal: {state["goal"]}

Known facts about the user (long-term memory):
{memory_block}

Tools you may use. Use only these tool names and argument names:
{describe_tools()}

Write a plan of at most {settings.max_steps} steps that achieves the goal with these tools.
Respond with ONLY a JSON object in this form:
{{"steps": [{{"tool": "<tool name>", "args": {{...}}, "description": "<one sentence>"}}]}}
If the goal cannot be achieved with these tools, respond with {{"steps": []}}."""


def _text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):  # some models return content blocks
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


# --------------------------------------------------------------------------- #
# Step execution helpers
# --------------------------------------------------------------------------- #

def _run_with_retries(fn: Callable[[], str], settings: Settings) -> tuple[bool, str]:
    attempts = settings.tool_retries + 1
    last_error = "unknown error"
    for attempt in range(1, attempts + 1):
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(fn)
        try:
            output = future.result(timeout=settings.tool_timeout_s)
            pool.shutdown(wait=False)
            return True, output
        except FuturesTimeout:
            # Python cannot kill a thread. The worker may finish later, which is
            # why every tool is written to be idempotent.
            pool.shutdown(wait=False, cancel_futures=True)
            last_error = f"timed out after {settings.tool_timeout_s}s"
        except Exception as exc:  # noqa: BLE001 - tool failures are data, not crashes
            pool.shutdown(wait=False)
            last_error = f"{type(exc).__name__}: {exc}"
        log.warning("attempt %d/%d failed: %s", attempt, attempts, last_error)
        if attempt < attempts:
            time.sleep(settings.retry_backoff_s * (2 ** (attempt - 1)))
    return False, last_error


def _pending_step(state: AgentState) -> dict[str, Any] | None:
    done = set(state.get("completed", []))
    for step in state.get("plan", []):
        if step["id"] not in done:
            return step
    return None


def _normalize_decision(value: Any) -> str:
    if value is True:
        return "approved"
    if isinstance(value, str) and value.strip().lower() in APPROVE_WORDS:
        return "approved"
    return "rejected"


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #

def build_graph(*, llm: Any, settings: Settings, checkpointer: Any):
    memory = LongTermMemory(settings.db_path)

    def recall(state: AgentState) -> dict[str, Any]:
        return {
            "memories": memory.recall(state["user_id"]),
            "completed": [],
            "decisions": {},
            "results": [],
            "status": "running",
            "error": None,
        }

    def plan(state: AgentState) -> dict[str, Any]:
        prompt = _build_planning_prompt(state, settings)
        last_error = ""
        for attempt in range(2):  # one correction round
            attempt_prompt = prompt
            if last_error:
                attempt_prompt += (
                    f"\n\nYour previous reply was rejected: {last_error}. "
                    "Reply again with corrected JSON only."
                )
            try:
                reply = _text(llm.invoke(attempt_prompt))
                steps = parse_plan(reply, settings.max_steps)
                return {"plan": steps, "status": "planned", "error": None}
            except PlanError as exc:
                last_error = str(exc)
                log.info("plan attempt %d rejected: %s", attempt + 1, exc)
            except Exception as exc:  # model unreachable, timeout, etc.
                return {"plan": [], "status": "failed", "error": f"planner error: {exc}"}
        return {"plan": [], "status": "failed", "error": f"could not build a valid plan: {last_error}"}

    def approval(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        step = _pending_step(state)
        assert step is not None
        if config.get("configurable", {}).get("unattended"):
            # Scheduled runs have nobody to answer, so risky steps are refused.
            decision = "rejected"
        else:
            answer = interrupt(
                {
                    "thread_id": state["thread_id"],
                    "step": step,
                    "message": "This step changes something. Approve it?",
                }
            )
            decision = _normalize_decision(answer)
        return {"decisions": {**state.get("decisions", {}), step["id"]: decision}}

    def execute(state: AgentState) -> dict[str, Any]:
        step = _pending_step(state)
        assert step is not None
        completed = [*state.get("completed", []), step["id"]]
        results = list(state.get("results", []))
        base = {"step_id": step["id"], "tool": step["tool"], "description": step["description"]}

        if state.get("decisions", {}).get(step["id"]) == "rejected":
            results.append({**base, "status": "skipped", "output": "not approved"})
            return {"completed": completed, "results": results}

        ctx = ToolContext(
            notes_dir=settings.notes_dir,
            db_path=settings.db_path,
            user_id=state["user_id"],
            thread_id=state["thread_id"],
        )
        spec = TOOLS[step["tool"]]
        try:
            clean = validate_args(step["tool"], step["args"])
        except ToolInputError as exc:
            results.append({**base, "status": "error", "output": f"invalid input: {exc}"})
            return {"completed": completed, "results": results, "status": "failed",
                    "error": f"step {step['id']} failed: invalid input"}

        ok, output = _run_with_retries(lambda: spec.fn(ctx, **clean), settings)
        if ok:
            results.append({**base, "status": "ok", "output": output})
            return {"completed": completed, "results": results}
        results.append({**base, "status": "error", "output": output})
        return {"completed": completed, "results": results, "status": "failed",
                "error": f"step {step['id']} ({step['tool']}) failed: {output}"}

    def finish(state: AgentState) -> dict[str, Any]:
        if state.get("status") == "failed":
            return {}
        skipped = any(r.get("status") == "skipped" for r in state.get("results", []))
        return {"status": "completed_with_skips" if skipped else "completed"}

    def route_next(state: AgentState) -> str:
        if state.get("status") == "failed":
            return "finish"
        step = _pending_step(state)
        if step is None:
            return "finish"
        needs_approval = TOOLS[step["tool"]].requires_approval
        if needs_approval and step["id"] not in state.get("decisions", {}):
            return "approval"
        return "execute"

    graph = StateGraph(AgentState)
    graph.add_node("recall", recall)
    graph.add_node("plan", plan)
    graph.add_node("approval", approval)
    graph.add_node("execute", execute)
    graph.add_node("finish", finish)

    graph.add_edge(START, "recall")
    graph.add_edge("recall", "plan")
    route_map = {"approval": "approval", "execute": "execute", "finish": "finish"}
    graph.add_conditional_edges("plan", route_next, route_map)
    graph.add_conditional_edges("approval", route_next, route_map)
    graph.add_conditional_edges("execute", route_next, route_map)
    graph.add_edge("finish", END)

    return graph.compile(checkpointer=checkpointer)
