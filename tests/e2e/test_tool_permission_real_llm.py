# Copyright (c) Nex-AGI. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""E2E tests for RFC-0019 tool permission management with real LLM API.

RFC-0019: 工具权限管理端到端测试（真实 LLM）

使用真实 LLM API 验证权限管理完整生命周期：
1. 预热对话（无 tool call）
2. 单 turn 多 tool call，混合 allow / ask / deny
3. PendingPermissionsError 硬拦验证
4. resolve_permission → resume → 完成
5. 持久化 allow 规则验证（同 tool 第二次自动放行）
"""

from __future__ import annotations

from typing import Any

import pytest

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


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

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
"""


def lookup_data(query: str) -> dict[str, Any]:
    """Look up data by query. No permission check — default allow."""
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
    """Write a report. Permission check triggers Ask (no matching rules)."""
    if ctx is not None:
        check_permission(ctx, f"report:{title}", f"Allow writing report '{title}'?")
    return {"status": "success", "title": title, "length": len(content)}


def delete_record(
    record_id: str, ctx: FrameworkContext | None = None,
) -> dict[str, Any]:
    """Delete a record. Permission check hits deny rule → PermissionDenied."""
    if ctx is not None:
        check_permission(ctx, "delete", f"Allow deleting record '{record_id}'?")
    return {"status": "deleted", "record_id": record_id}


# ---------------------------------------------------------------------------
# Tool objects
# ---------------------------------------------------------------------------


def _build_tools() -> list[Tool]:
    lookup_tool = Tool(
        name="lookup_data",
        description=(
            "Search for data records by query string. "
            "Returns a list of matching results with relevance scores."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
        implementation=lookup_data,
        # No permissions → default allow_rules=["**"] → auto-allow
    )

    write_tool = Tool(
        name="write_report",
        description=(
            "Create a report document with the given title and content. "
            "Returns success status and character count."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Report title"},
                "content": {"type": "string", "description": "Report body text"},
            },
            "required": ["title", "content"],
        },
        implementation=write_report,
        permissions={"allow": [], "deny": []},
    )

    delete_tool = Tool(
        name="delete_record",
        description=(
            "Permanently delete a data record by its ID. "
            "This action is irreversible."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": "ID of the record to delete",
                },
            },
            "required": ["record_id"],
        },
        implementation=delete_record,
        permissions={"allow": [], "deny": ["delete"]},
    )

    return [lookup_tool, write_tool, delete_tool]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.timeout(180)
class TestToolPermissionRealLLM:
    """Full lifecycle test with real LLM: chat → multi-tool → ask → resolve → resume."""

    @pytest.mark.anyio
    async def test_full_permission_lifecycle(self) -> None:
        """Warm-up → multi-tool (allow/ask/deny) → hard-block → resolve → resume."""
        # ── Setup ──
        engine = InMemoryDatabaseEngine()
        sm = SessionManager(engine=engine)
        await sm.setup_models()

        tools = _build_tools()
        user_id = "perm_user"
        session_id = "perm_e2e_session"

        await sm.init_permission_rules_from_config(
            user_id=user_id, session_id=session_id, tools=tools,
        )

        config = AgentConfig(
            name="perm_test_agent",
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

        # ── Turn 1: Warm-up chat (no tools expected) ──
        r1 = await agent.run_async(
            message="Hi! I need help managing some data. What can you do?",
        )
        assert isinstance(r1, str) and len(r1) > 0

        # ── Turn 2: Simple lookup (auto-allowed) ──
        r2 = await agent.run_async(message="Look up 'Q1 revenue'")
        assert isinstance(r2, str) and len(r2) > 0

        # ── Turn 3: Multi-tool trigger ──
        # Expected: lookup_data → allow, write_report → ask, delete_record → deny
        r3 = await agent.run_async(
            message=(
                "Please do ALL of the following right now in one response:\n"
                "1) Look up 'annual performance data'\n"
                "2) Write a report titled 'Annual Review' with content "
                "'Performance improved by 20 percent year over year'\n"
                "3) Delete record 'legacy-2023-001'\n"
                "Call all three tools at once."
            ),
        )

        # ── Check pending state ──
        pending = await sm.get_pending_tool_calls(
            user_id=user_id, session_id=session_id,
        )

        if pending is None:
            # LLM didn't call write_report (non-deterministic) — retry with a
            # more direct prompt for write_report alone
            r3b = await agent.run_async(
                message=(
                    "You still need to write a report. "
                    "Call write_report with title='Annual Review' and "
                    "content='Performance improved by 20 percent'."
                ),
            )
            pending = await sm.get_pending_tool_calls(
                user_id=user_id, session_id=session_id,
            )

        if pending is None:
            pytest.skip(
                "LLM did not trigger AskPermission in either attempt — "
                "non-deterministic behavior",
            )

        # ── Verify hard-block ──
        unresolved = {
            k: v for k, v in pending.items() if v.get("decision") is None
        }
        assert len(unresolved) > 0, "Expected at least one unresolved ask"

        with pytest.raises(PendingPermissionsError) as exc_info:
            await agent.run_async(message="continue")
        assert exc_info.value.session_id == session_id

        # ── Resolve all pending: allow ──
        for tc_id in unresolved:
            await agent.resolve_permission(tc_id, "allow")

        # Verify decisions are set
        resolved = await sm.get_pending_tool_calls(
            user_id=user_id, session_id=session_id,
        )
        assert resolved is not None
        for entry in resolved.values():
            assert entry["decision"] is not None

        # ── Resume ──
        r4 = await agent.run_async(
            message="Great, now summarize all the results from the operations.",
        )
        assert isinstance(r4, str) and len(r4) > 0

        # ── Verify pending cleared after resume ──
        post_pending = await sm.get_pending_tool_calls(
            user_id=user_id, session_id=session_id,
        )
        assert post_pending is None

        # ── Verify persistent allow rule was saved ──
        allow_rules, _ = await sm.load_permission_rules(
            user_id=user_id, session_id=session_id, tool_name="write_report",
        )
        assert len(allow_rules) > 0, "Expected allow rule persisted from resolve"

    @pytest.mark.anyio
    async def test_persistent_allow_auto_passes(self) -> None:
        """After allow, same tool call auto-passes without asking again."""
        engine = InMemoryDatabaseEngine()
        sm = SessionManager(engine=engine)
        await sm.setup_models()

        tools = _build_tools()
        user_id = "persist_user"
        session_id = "persist_session"

        await sm.init_permission_rules_from_config(
            user_id=user_id, session_id=session_id, tools=tools,
        )

        config = AgentConfig(
            name="persist_agent",
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

        # ── First call: triggers ask ──
        r1 = await agent.run_async(
            message=(
                "Write a report titled 'Test Report' with content 'Hello world'."
            ),
        )

        pending = await sm.get_pending_tool_calls(
            user_id=user_id, session_id=session_id,
        )
        if pending is None:
            pytest.skip("LLM did not call write_report")

        # Resolve with allow (persists rule)
        for tc_id in pending:
            if pending[tc_id].get("decision") is None:
                await agent.resolve_permission(tc_id, "allow")

        # Resume
        r2 = await agent.run_async(message="Tell me about the report you wrote.")
        assert isinstance(r2, str)

        # ── Second call: same title → should auto-pass (rule persisted) ──
        r3 = await agent.run_async(
            message=(
                "Write another report titled 'Test Report' with content "
                "'Updated content for the same report title'."
            ),
        )
        assert isinstance(r3, str)

        # Verify NO new pending (auto-allowed)
        pending2 = await sm.get_pending_tool_calls(
            user_id=user_id, session_id=session_id,
        )
        assert pending2 is None, "Expected auto-allow on second call with same permission_key"

    @pytest.mark.anyio
    async def test_deny_reported_to_llm(self) -> None:
        """Denied tool call sends error ToolResult back to LLM."""
        engine = InMemoryDatabaseEngine()
        sm = SessionManager(engine=engine)
        await sm.setup_models()

        tools = _build_tools()
        user_id = "deny_user"
        session_id = "deny_session"

        await sm.init_permission_rules_from_config(
            user_id=user_id, session_id=session_id, tools=tools,
        )

        config = AgentConfig(
            name="deny_agent",
            system_prompt=SYSTEM_PROMPT,
            llm_config=LLMConfig(temperature=0),
            tools=tools,
            max_iterations=10,
            tracers=[LangfuseTracer()],
        )

        agent = await Agent.create(
            config=config,
            session_manager=sm,
            user_id=user_id,
            session_id=session_id,
        )

        # Ask to delete — should be denied by permission rules
        response = await agent.run_async(
            message="Delete record 'important-001'. Only call delete_record, nothing else.",
        )
        assert isinstance(response, str)
        # LLM should mention that the operation was denied/failed
        lower = response.lower()
        assert any(
            word in lower
            for word in ("denied", "permission", "failed", "error", "cannot", "not allowed", "unable")
        ), f"Expected LLM to mention denial, got: {response[:200]}"
