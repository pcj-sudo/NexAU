"""Interactive demo for RFC-0019 tool permission management — full built-in tools.

Usage:
    HTTP_PROXY="" uv run python scripts/demo_permission_full.py

覆盖 NexAU 全部内置工具的权限管理测试。
详细测试计划见 docs/testing/permission-e2e-test-plan.md
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
from nexau.archs.session import SessionManager
from nexau.archs.session.orm import InMemoryDatabaseEngine
from nexau.archs.tool.tool import Tool
from nexau.archs.tracer.adapters.langfuse import LangfuseTracer

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).resolve().parent.parent / "examples" / "code_agent" / "tools"

WORKSPACE = "/tmp/nexau_perm_test/workspace"

SYSTEM_PROMPT = f"""\
You are a coding assistant with full access to built-in tools.
Your working directory is {WORKSPACE}.

RULES:
- When asked to do multiple things, call ALL tools in ONE response.
- If a tool call fails due to permission, report it and continue.
- Do NOT ask for confirmation — just call tools directly.
- Keep responses concise. Reply in Chinese.
"""


# ---------------------------------------------------------------------------
# Tool builder
# ---------------------------------------------------------------------------


def _build_tools() -> list[Tool]:
    """Build all built-in tools with permission configurations."""
    from nexau.archs.tool.builtin.file_tools import (
        apply_patch,
        list_directory,
        read_file,
        read_many_files,
        replace,
        search_file_content,
        write_file,
    )
    from nexau.archs.tool.builtin.shell_tools import run_shell_command
    from nexau.archs.tool.builtin.web_tools import google_web_search, web_fetch

    tools: list[Tool] = []

    # ── 只读工具：无 permissions → 默认 allow_rules=["**"] ──

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "read_file.tool.yaml"),
        binding=read_file,
    ))

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "read_many_files.tool.yaml"),
        binding=read_many_files,
    ))

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "list_directory.tool.yaml"),
        binding=list_directory,
    ))

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "search_file_content.tool.yaml"),
        binding=search_file_content,
    ))

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "WebSearch.tool.yaml"),
        binding=google_web_search,
    ))

    # ── 文件写入工具：路径级 allow / deny ──

    file_write_permissions = {
        "allow": [f"{WORKSPACE}/src/**"],
        "deny": [".env"],
    }

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "write_file.tool.yaml"),
        binding=write_file,
        permissions=file_write_permissions,
    ))

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "replace.tool.yaml"),
        binding=replace,
        permissions=file_write_permissions,
    ))

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "apply_patch.tool.yaml"),
        binding=apply_patch,
        permissions=file_write_permissions,
    ))

    # ── Shell：只读白名单 + allow python + deny rm/sudo ──

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "run_shell_command.tool.yaml"),
        binding=run_shell_command,
        permissions={
            "allow": ["python", "python3", "node"],
            "deny": ["rm", "sudo", "chmod", "chown"],
        },
    ))

    # ── Web Fetch：域名级 allow / deny ──

    tools.append(Tool.from_yaml(
        str(TOOLS_DIR / "WebFetch.tool.yaml"),
        binding=web_fetch,
        permissions={
            "allow": ["github.com", "*.github.com", "pypi.org"],
            "deny": ["evil.com", "*.evil.com"],
        },
    ))

    return tools


# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------


def _setup_workspace() -> None:
    """Create test workspace with sample files."""
    ws = Path(WORKSPACE)
    src = ws / "src"
    src.mkdir(parents=True, exist_ok=True)

    (src / "main.py").write_text("hello world\n")
    (ws / ".env").write_text("SECRET=abc123\n")
    (ws / "data.txt").write_text("line1\nline2\nline3\n")

    print(f"Workspace ready: {WORKSPACE}")
    print(f"  src/main.py  → 'hello world'")
    print(f"  .env         → 'SECRET=abc123'")
    print(f"  data.txt     → 3 lines")


# ---------------------------------------------------------------------------
# Interactive loop
# ---------------------------------------------------------------------------


async def main() -> None:
    _setup_workspace()

    engine = InMemoryDatabaseEngine()
    sm = SessionManager(engine=engine)
    await sm.setup_models()

    tools = _build_tools()
    user_id = "test_user"
    session_id = "perm_full_test"

    await sm.init_permission_rules_from_config(
        user_id=user_id, session_id=session_id, tools=tools,
    )

    config = AgentConfig(
        name="permission_full_test",
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
    print("RFC-0019 Full Permission Test")
    print("=" * 60)
    print()
    print("Permission rules:")
    print("  read_file, list_dir, search, WebSearch  → auto-allow (no permissions)")
    print(f"  write_file, replace, apply_patch        → allow: {WORKSPACE}/src/**  deny: .env")
    print("  run_shell_command                       → allow: python  deny: rm,sudo  readonly: auto")
    print("  WebFetch                                → allow: github.com  deny: evil.com")
    print()
    print("Suggested test steps (see docs/testing/permission-e2e-test-plan.md):")
    print("  Phase 1: 读取 src/main.py")
    print("  Phase 2: 创建 src/utils.py → 修改 .env → 创建 README.md")
    print("  Phase 3: ls -la → git log → rm data.txt → curl example.com")
    print("  Phase 4: 抓取 github.com → 抓取 evil.com → 抓取 example.com")
    print("  Phase 7: 同时读文件 + 创建文件 + rm 文件")
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


if __name__ == "__main__":
    asyncio.run(main())
