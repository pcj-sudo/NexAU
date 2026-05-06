"""Interactive demo for CC-aligned agent with E2B sandbox + RFC-0019 permissions.

Usage:
    # Set E2B credentials
    export E2B_API_URL="https://hk-prod-e2b.xiaobei.top"
    export E2B_API_KEY="your_key"
    export E2B_DOMAIN="hk-prod-e2b.xiaobei.top"

    # Run
    HTTP_PROXY="" uv run python scripts/demo_cc_agent.py

Full tool list (15 tools, CC-aligned permissions):
  - Readonly (auto-allow): read_file, read_many_files, list_directory,
    search_file_content, web_search
  - File write (path-level): write_file, replace, apply_patch
  - Shell (readonly whitelist + command-level): run_shell_command
  - Code execution (every call ask): run_code_tool
  - Web fetch (domain-level): web_fetch
  - Session (auto-allow): save_memory, write_todos, complete_task, ask_user

Test plan: docs/testing/permission-e2e-test-plan.md
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

load_dotenv()

from nexau.archs.llm.llm_config import LLMConfig
from nexau.archs.main_sub.agent import Agent
from nexau.archs.main_sub.config import AgentConfig
from nexau.archs.permissions.types import PendingPermissionsError
from nexau.archs.sandbox.base_sandbox import E2BSandboxConfig
from nexau.archs.session import SessionManager
from nexau.archs.session.orm import InMemoryDatabaseEngine
from nexau.archs.tool.tool import Tool
from nexau.archs.tracer.adapters.langfuse import LangfuseTracer

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).resolve().parent.parent / "examples" / "cc_agent" / "tools"

SYSTEM_PROMPT = """\
You are a coding assistant with access to a sandboxed development environment.

RULES:
- When asked to do multiple things, call ALL tools in ONE response.
- If a tool call fails due to permission, report it and continue.
- Do NOT ask for confirmation — just call tools directly.
- Keep responses concise. Reply in Chinese.

Working directory: {work_dir}
"""

# ---------------------------------------------------------------------------
# E2B sandbox config
# ---------------------------------------------------------------------------


def _build_e2b_config() -> E2BSandboxConfig:
    api_key = os.getenv("E2B_API_KEY")
    if not api_key:
        print("ERROR: E2B_API_KEY not set.")
        print("  export E2B_API_URL='https://hk-prod-e2b.xiaobei.top'")
        print("  export E2B_API_KEY='your_key'")
        print("  export E2B_DOMAIN='hk-prod-e2b.xiaobei.top'")
        sys.exit(1)

    return E2BSandboxConfig(
        type="e2b",
        api_key=api_key,
        api_url=os.getenv("E2B_API_URL") or None,
        template=os.getenv("E2B_TEMPLATE", "base"),
        timeout=int(os.getenv("E2B_TIMEOUT", "300")),
        work_dir=os.getenv("E2B_WORK_DIR", "/home/user"),
        metadata={"example": "cc_agent", "launcher": "demo_cc_agent.py"},
    )


# ---------------------------------------------------------------------------
# Tool builder
# ---------------------------------------------------------------------------


def _build_tools() -> list[Tool]:
    """Build all CC-aligned tools with permission configurations."""
    from nexau.archs.tool.builtin.file_tools import (
        apply_patch,
        glob,
        list_directory,
        read_file,
        read_many_files,
        read_visual_file,
        replace,
        search_file_content,
        write_file,
    )
    from nexau.archs.tool.builtin.multiedit_tool import multiedit_tool
    from nexau.archs.tool.builtin.run_code_tool import run_code_tool
    from nexau.archs.tool.builtin.shell_tools import run_shell_command
    from nexau.archs.tool.builtin.web_tools import google_web_search, web_fetch

    tools: list[Tool] = []

    # ── Readonly tools: no permissions → default allow_rules=["**"] ──

    tools.append(Tool.from_yaml(str(TOOLS_DIR / "read_file.tool.yaml"), binding=read_file))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "read_many_files.tool.yaml"), binding=read_many_files))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "read_visual_file.tool.yaml"), binding=read_visual_file))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "glob.tool.yaml"), binding=glob))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "list_directory.tool.yaml"), binding=list_directory))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "search_file_content.tool.yaml"), binding=search_file_content))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "WebSearch.tool.yaml"), binding=google_web_search))

    # ── File write tools: path-level allow / deny ──

    empty_permissions = {"allow": [], "deny": []}

    tools.append(Tool.from_yaml(str(TOOLS_DIR / "write_file.tool.yaml"), binding=write_file, permissions=empty_permissions))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "replace.tool.yaml"), binding=replace, permissions=empty_permissions))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "apply_patch.tool.yaml"), binding=apply_patch, permissions=empty_permissions))
    tools.append(Tool.from_yaml(str(TOOLS_DIR / "multiedit_tool.tool.yaml"), binding=multiedit_tool, permissions=empty_permissions))

    # ── Shell: readonly whitelist auto-allow, all else ask ──

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "run_shell_command.tool.yaml"),
        binding=run_shell_command,
        permissions=empty_permissions,
    ))

    # ── Code execution: every call ask ──

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "run_code_tool.tool.yaml"),
        binding=run_code_tool,
        permissions={"allow": [], "deny": []},
    ))

    # ── Web fetch: domain-level, start empty (every domain asks) ──

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "WebFetch.tool.yaml"),
        binding=web_fetch,
        permissions={"allow": [], "deny": []},
    ))

    # NOTE: BackgroundTaskManage, sub_agent (call_sub_agent), tool_search,
    # skill_tool 由框架根据 AgentConfig 自动注册，不需要手动 Tool.from_yaml。

    return tools


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------


async def main() -> None:
    e2b_config = _build_e2b_config()

    engine = InMemoryDatabaseEngine()
    sm = SessionManager(engine=engine)
    await sm.setup_models()

    tools = _build_tools()
    user_id = "test_user"
    session_id = "cc_agent_test"

    await sm.init_permission_rules_from_config(
        user_id=user_id, session_id=session_id, tools=tools,
    )

    config = AgentConfig(
        name="cc_agent_permission_test",
        system_prompt=SYSTEM_PROMPT.format(work_dir=e2b_config.work_dir),
        llm_config=LLMConfig(temperature=0),
        tools=tools,
        max_iterations=15,
        sandbox_config=e2b_config,
        tracers=[LangfuseTracer()],
    )

    agent = await Agent.create(
        config=config,
        session_manager=sm,
        user_id=user_id,
        session_id=session_id,
    )

    print(f"E2B sandbox will start lazily on first tool call.")
    print(f"Working directory: {e2b_config.work_dir}")

    # ── Helper: check and resolve pending permissions ──

    async def _check_and_resolve_pending() -> bool:
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
        print("-" * 50)

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

        print("-" * 50)
        print("Permissions resolved. Resuming...")
        print()
        return True

    # ── Print header ──

    print()
    print("=" * 60)
    print("CC-Aligned Agent — Full Permission Test (E2B Sandbox)")
    print("=" * 60)
    print()
    print("Tools (19 YAML + 3 framework-auto):")
    print("  Readonly (auto-allow):  read_file, read_many_files, read_visual_file,")
    print("                          glob, list_directory, search_file_content, web_search")
    print("  File write (all ask):   write_file, replace, apply_patch, multiedit_tool")
    print("  Shell (whitelist+ask):  run_shell_command  (readonly cmds auto, rest ask)")
    print("  Shell helper:           BackgroundTaskManage")
    print("  Code exec (always ask): run_code_tool")
    print("  Web fetch (domain ask): web_fetch")
    print("  Session (auto-allow):   save_memory, write_todos, complete_task, ask_user")
    print("  Framework-auto:         sub_agent (explore), tool_search, skill_tool")
    print()
    print("CC-aligned: no hardcoded deny — user decides via allow/deny responses.")
    print()
    print("Suggested tests:")
    print("  1. 列出 /home/user 目录                        (readonly → auto)")
    print("  2. 创建 /home/user/hello.py 写 print('hi')    (write → ask)")
    print("  3. 用 run_code_tool 执行 print(1+1)           (code → ask)")
    print("  4. 执行 ls -la /home/user                      (shell readonly → auto)")
    print("  5. 执行 rm /home/user/hello.py                 (shell → ask)")
    print("  6. 执行 python hello.py                        (shell → ask)")
    print("  7. 抓取 https://example.com                    (web → ask)")
    print("  8. 同时: 读文件 + 写文件 + rm 文件             (parallel mixed)")
    print()
    print("Type 'quit' to exit.")
    print("=" * 60)

    # ── Main loop ──

    while True:
        print()
        user_input = input("\033[36mYou: \033[0m").strip()
        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            print("Bye!")
            break

        try:
            response = await agent.run_async(message=user_input)
            print(f"\033[33mAgent:\033[0m {response}")

            if await _check_and_resolve_pending():
                response = await agent.run_async(
                    message="Continue with the results from the operations.",
                )
                print(f"\033[33mAgent:\033[0m {response}")

        except PendingPermissionsError:
            if await _check_and_resolve_pending():
                response = await agent.run_async(
                    message="Continue with the results from the operations.",
                )
                print(f"\033[33mAgent:\033[0m {response}")

        except KeyboardInterrupt:
            print("\nBye!")
            break
        except Exception as e:
            print(f"\033[31mError: {e}\033[0m")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
