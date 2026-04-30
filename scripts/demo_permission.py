"""Interactive demo for RFC-0019 tool permission management.

Usage:
    HTTP_PROXY="" uv run python scripts/demo_permission.py

按测试 1 的流程对话：
  1. "Hi! What can you do?"                          → 纯聊天
  2. "Look up 'Q1 revenue'"                           → 自动放行
  3. "同时查数据、写报告、删记录"（一轮三工具）       → allow/ask/deny 混合
  4. 弹出权限请求 → 输入 allow/deny                   → resolve
  5. 继续对话                                         → resume
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from nexau.archs.llm.llm_config import LLMConfig
from nexau.archs.main_sub.agent import Agent
from nexau.archs.main_sub.config import AgentConfig
from nexau.archs.main_sub.framework_context import FrameworkContext
from nexau.archs.permissions import check_permission
from nexau.archs.permissions.types import PendingPermissionsError
from nexau.archs.session import SessionManager
from nexau.archs.session.orm import InMemoryDatabaseEngine
from nexau.archs.tool.tool import Tool
from nexau.archs.tracer.adapters.langfuse import LangfuseTracer


SYSTEM_PROMPT = """\
You are a data management assistant. You have exactly 3 tools:

1. lookup_data - search for data records (always works, no restrictions)
2. write_report - create a report document (may require approval)
3. delete_record - permanently delete a record (restricted operation)

CRITICAL RULES:
- When the user asks you to perform multiple operations, you MUST call ALL \
requested tools in a SINGLE response. Never do them one at a time.
- Use the exact parameters the user specifies.
- If a tool call is denied or fails, report it but continue with other results.
- Do NOT ask for confirmation before calling tools — just call them directly.
- Keep responses concise.
- Reply in Chinese.
"""


def lookup_data(query: str) -> dict[str, Any]:
    return {
        "results": [
            {"id": "r1", "title": f"Result 1 for '{query}'", "score": 0.95},
            {"id": "r2", "title": f"Result 2 for '{query}'", "score": 0.82},
        ],
        "total": 2,
    }


def write_report(
    title: str, content: str, ctx: FrameworkContext | None = None,
) -> dict[str, Any]:
    if ctx is not None:
        check_permission(ctx, f"report:{title}", f"Allow writing report '{title}'?")
    return {"status": "success", "title": title, "length": len(content)}


def delete_record(
    record_id: str, ctx: FrameworkContext | None = None,
) -> dict[str, Any]:
    if ctx is not None:
        check_permission(ctx, "delete", f"Allow deleting record '{record_id}'?")
    return {"status": "deleted", "record_id": record_id}


def _build_tools() -> list[Tool]:
    return [
        Tool(
            name="lookup_data",
            description="Search for data records by query string.",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            implementation=lookup_data,
        ),
        Tool(
            name="write_report",
            description="Create a report document with title and content.",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title", "content"],
            },
            implementation=write_report,
            permissions={"allow": [], "deny": []},
        ),
        Tool(
            name="delete_record",
            description="Permanently delete a data record by ID. Irreversible.",
            input_schema={
                "type": "object",
                "properties": {"record_id": {"type": "string"}},
                "required": ["record_id"],
            },
            implementation=delete_record,
            permissions={"allow": [], "deny": ["delete"]},
        ),
    ]


async def main() -> None:
    engine = InMemoryDatabaseEngine()
    sm = SessionManager(engine=engine)
    await sm.setup_models()

    tools = _build_tools()
    user_id = "demo_user"
    session_id = "demo_session"

    await sm.init_permission_rules_from_config(
        user_id=user_id, session_id=session_id, tools=tools,
    )

    config = AgentConfig(
        name="permission_demo",
        system_prompt=SYSTEM_PROMPT,
        llm_config=LLMConfig(temperature=0),
        tools=tools,
        max_iterations=15,
        tracers=[LangfuseTracer()],
    )

    agent = await Agent.create(
        config=config,
        session_manager=sm,
        user_id=user_id,
        session_id=session_id,
    )

    print("=" * 60)
    print("RFC-0019 Permission Demo")
    print("=" * 60)
    print("Tools:")
    print("  lookup_data   → auto-allow (no permissions)")
    print("  write_report  → ask (empty rules)")
    print("  delete_record → deny (deny rule: 'delete')")
    print()
    print("Suggested flow:")
    print("  1. Hi! What can you do?")
    print("  2. Look up 'Q1 revenue'")
    print("  3. 同时：查 'annual data'、写报告 'Annual Review'")
    print("     内容 '20% growth'、删记录 'legacy-2023'")
    print()
    print("Type 'quit' to exit.")
    print("=" * 60)

    async def _check_and_resolve_pending() -> bool:
        """Check for pending permissions and prompt user to resolve.

        Returns True if permissions were resolved (caller should resume).
        """
        pending = await sm.get_pending_tool_calls(
            user_id=user_id, session_id=session_id,
        )
        if not pending:
            return False

        unresolved = {
            k: v for k, v in pending.items()
            if v.get("decision") is None
        }
        if not unresolved:
            return False

        print()
        print("\033[31m⚠ Permission required!\033[0m")
        print("-" * 40)

        for tc_id, entry in unresolved.items():
            print(f"  Tool:   {entry['tool_name']}")
            print(f"  Prompt: {entry['prompt']}")
            print(f"  ID:     {tc_id}")
            print()

            while True:
                choice = input("  → allow / allow_once / deny: ").strip().lower()
                if choice in ("allow", "allow_once", "deny"):
                    break
                print("    (please type: allow, allow_once, or deny)")

            await agent.resolve_permission(tc_id, choice)
            print(f"  ✓ Resolved: {choice}")
            print()

        print("-" * 40)
        print("Permissions resolved. Resuming...")
        print()
        return True

    while True:
        print()
        user_input = input("\033[36mYou: \033[0m").strip()
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break

        try:
            response = await agent.run_async(message=user_input)
            print(f"\033[33mAgent:\033[0m {response}")

            # RFC-0019: run 结束后立即检查是否有 pending 权限请求
            if await _check_and_resolve_pending():
                response = await agent.run_async(
                    message="Continue with the results from the operations.",
                )
                print(f"\033[33mAgent:\033[0m {response}")

        except PendingPermissionsError:
            # 上一轮遗留的 pending 未决 — 先 resolve 再 resume
            if await _check_and_resolve_pending():
                response = await agent.run_async(
                    message="Continue with the results from the operations.",
                )
                print(f"\033[33mAgent:\033[0m {response}")

        except Exception as e:
            print(f"\033[31mError: {e}\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
