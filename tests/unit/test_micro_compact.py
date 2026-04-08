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

"""Tests for micro-compact: TimeBasedTrigger, ToolResultCompaction compactable_tools,
CompactionConfig new fields, and Message.created_at."""

from datetime import UTC, datetime, timedelta

import pytest

from nexau.archs.main_sub.execution.middleware.context_compaction import (
    TimeBasedTrigger,
    ToolResultCompaction,
)
from nexau.archs.main_sub.execution.middleware.context_compaction.config import CompactionConfig
from nexau.archs.main_sub.execution.middleware.context_compaction.factory import (
    create_compaction_strategy,
    create_trigger_strategy,
)
from nexau.core.messages import Message, Role, TextBlock, ToolResultBlock, ToolUseBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assistant_msg(text: str, created_at: datetime | None = None, tool_calls: list[ToolUseBlock] | None = None) -> Message:
    blocks: list[TextBlock | ToolUseBlock] = [TextBlock(text=text)]
    if tool_calls:
        blocks.extend(tool_calls)
    return Message(role=Role.ASSISTANT, content=blocks, created_at=created_at)


def _tool_result_msg(tool_use_id: str, content: str = "some output") -> Message:
    return Message(
        role=Role.TOOL,
        content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=False)],
    )


def _user_msg(text: str = "hello") -> Message:
    return Message(role=Role.USER, content=[TextBlock(text=text)])


def _system_msg(text: str = "You are helpful.") -> Message:
    return Message(role=Role.SYSTEM, content=[TextBlock(text=text)])


# ---------------------------------------------------------------------------
# TimeBasedTrigger
# ---------------------------------------------------------------------------


class TestTimeBasedTrigger:
    """Tests for TimeBasedTrigger strategy."""

    def test_init_default(self):
        trigger = TimeBasedTrigger()
        assert trigger.gap_threshold == timedelta(minutes=5)

    def test_init_custom(self):
        trigger = TimeBasedTrigger(gap_threshold_minutes=10)
        assert trigger.gap_threshold == timedelta(minutes=10)

    def test_triggers_when_gap_exceeds_threshold(self):
        """Last assistant message created_at 8 min ago -> triggers."""
        trigger = TimeBasedTrigger(gap_threshold_minutes=5)
        msgs = [
            _user_msg(),
            _assistant_msg("hi", created_at=datetime.now(UTC) - timedelta(minutes=8)),
        ]
        should, reason = trigger.should_compact(msgs, 1000, 10000)
        assert should is True
        assert "8." in reason or "7." in reason  # ~8 min gap

    def test_no_trigger_when_below_threshold(self):
        """Last assistant message created_at 2 min ago -> no trigger."""
        trigger = TimeBasedTrigger(gap_threshold_minutes=5)
        msgs = [
            _user_msg(),
            _assistant_msg("hi", created_at=datetime.now(UTC) - timedelta(minutes=2)),
        ]
        should, reason = trigger.should_compact(msgs, 1000, 10000)
        assert should is False
        assert reason == ""

    def test_no_trigger_when_no_assistant_messages(self):
        """New session with no assistant messages -> no trigger."""
        trigger = TimeBasedTrigger(gap_threshold_minutes=5)
        msgs = [_user_msg()]
        should, reason = trigger.should_compact(msgs, 1000, 10000)
        assert should is False

    def test_no_trigger_when_created_at_is_none(self):
        """Assistant message with created_at=None -> no trigger (safe default)."""
        trigger = TimeBasedTrigger(gap_threshold_minutes=5)
        msgs = [_user_msg(), _assistant_msg("hi", created_at=None)]
        should, reason = trigger.should_compact(msgs, 1000, 10000)
        assert should is False

    def test_stateless_same_result(self):
        """Same messages + same time -> same result (statelessness)."""
        trigger = TimeBasedTrigger(gap_threshold_minutes=5)
        msgs = [
            _user_msg(),
            _assistant_msg("hi", created_at=datetime.now(UTC) - timedelta(minutes=8)),
        ]
        r1 = trigger.should_compact(msgs, 1000, 10000)
        r2 = trigger.should_compact(msgs, 1000, 10000)
        assert r1[0] == r2[0]

    def test_uses_last_assistant_message(self):
        """Multiple assistant messages -> uses the last one."""
        trigger = TimeBasedTrigger(gap_threshold_minutes=5)
        msgs = [
            _user_msg(),
            _assistant_msg("old", created_at=datetime.now(UTC) - timedelta(minutes=30)),
            _user_msg(),
            _assistant_msg("recent", created_at=datetime.now(UTC) - timedelta(minutes=1)),
        ]
        should, _ = trigger.should_compact(msgs, 1000, 10000)
        assert should is False  # last assistant is 1 min ago

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (no tzinfo) is treated as UTC for comparison."""
        trigger = TimeBasedTrigger(gap_threshold_minutes=5)
        naive_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
        msgs = [_assistant_msg("hi", created_at=naive_time)]
        should, _ = trigger.should_compact(msgs, 1000, 10000)
        assert should is True


# ---------------------------------------------------------------------------
# ToolResultCompaction with compactable_tools
# ---------------------------------------------------------------------------


class TestToolResultCompactionWithFilter:
    """Tests for ToolResultCompaction compactable_tools filtering."""

    def _build_messages(self) -> list[Message]:
        """Build a message list with two iterations of tool calls.

        Iteration 1 (old): assistant calls read_file + write_file
        Iteration 2 (recent): assistant calls read_file
        """
        return [
            _system_msg(),
            # --- iteration 1 (old) ---
            _user_msg("read and write"),
            _assistant_msg(
                "I'll read and write.",
                tool_calls=[
                    ToolUseBlock(id="call_1", name="read_file", input={"path": "a.py"}),
                    ToolUseBlock(id="call_2", name="write_file", input={"path": "a.py", "content": "x"}),
                ],
            ),
            _tool_result_msg("call_1", "file content of a.py"),
            _tool_result_msg("call_2", "wrote a.py"),
            # --- iteration 2 (recent, protected) ---
            _user_msg("read again"),
            _assistant_msg(
                "I'll read.",
                tool_calls=[
                    ToolUseBlock(id="call_3", name="read_file", input={"path": "b.py"}),
                ],
            ),
            _tool_result_msg("call_3", "file content of b.py"),
        ]

    def test_compactable_tools_none_compacts_all(self):
        """compactable_tools=None -> compacts all old tool results (backward compat)."""
        strategy = ToolResultCompaction(keep_iterations=1, compactable_tools=None)
        msgs = self._build_messages()
        result = strategy.compact(msgs)

        # iteration 1 tool results should be compacted
        call_1_result = next(
            m for m in result if m.role == Role.TOOL and any(isinstance(b, ToolResultBlock) and b.tool_use_id == "call_1" for b in m.content)
        )
        tr = next(b for b in call_1_result.content if isinstance(b, ToolResultBlock))
        assert tr.content == "Tool call result has been compacted"

        call_2_result = next(
            m for m in result if m.role == Role.TOOL and any(isinstance(b, ToolResultBlock) and b.tool_use_id == "call_2" for b in m.content)
        )
        tr2 = next(b for b in call_2_result.content if isinstance(b, ToolResultBlock))
        assert tr2.content == "Tool call result has been compacted"

    def test_compactable_tools_filters_by_name(self):
        """compactable_tools={"read_file"} -> only compacts read_file results."""
        strategy = ToolResultCompaction(
            keep_iterations=1,
            compactable_tools=frozenset(["read_file"]),
        )
        msgs = self._build_messages()
        result = strategy.compact(msgs)

        # call_1 (read_file, old iteration) -> compacted
        call_1_result = next(
            m for m in result if m.role == Role.TOOL and any(isinstance(b, ToolResultBlock) and b.tool_use_id == "call_1" for b in m.content)
        )
        tr1 = next(b for b in call_1_result.content if isinstance(b, ToolResultBlock))
        assert tr1.content == "Tool call result has been compacted"

        # call_2 (write_file, old iteration) -> NOT compacted (not in compactable_tools)
        call_2_result = next(
            m for m in result if m.role == Role.TOOL and any(isinstance(b, ToolResultBlock) and b.tool_use_id == "call_2" for b in m.content)
        )
        tr2 = next(b for b in call_2_result.content if isinstance(b, ToolResultBlock))
        assert tr2.content == "wrote a.py"

        # call_3 (read_file, recent/protected iteration) -> NOT compacted (protected)
        call_3_result = next(
            m for m in result if m.role == Role.TOOL and any(isinstance(b, ToolResultBlock) and b.tool_use_id == "call_3" for b in m.content)
        )
        tr3 = next(b for b in call_3_result.content if isinstance(b, ToolResultBlock))
        assert tr3.content == "file content of b.py"

    def test_protected_iterations_not_compacted(self):
        """Recent iterations are protected regardless of compactable_tools."""
        strategy = ToolResultCompaction(
            keep_iterations=2,
            compactable_tools=frozenset(["read_file"]),
        )
        msgs = self._build_messages()
        result = strategy.compact(msgs)

        # Both iterations protected (keep_iterations=2) -> nothing compacted
        for m in result:
            if m.role == Role.TOOL:
                for b in m.content:
                    if isinstance(b, ToolResultBlock):
                        assert b.content != "Tool call result has been compacted"

    def test_empty_compactable_tools_compacts_nothing(self):
        """compactable_tools=frozenset() -> no tools are compactable."""
        strategy = ToolResultCompaction(
            keep_iterations=1,
            compactable_tools=frozenset(),
        )
        msgs = self._build_messages()
        result = strategy.compact(msgs)

        for m in result:
            if m.role == Role.TOOL:
                for b in m.content:
                    if isinstance(b, ToolResultBlock):
                        assert b.content != "Tool call result has been compacted"


# ---------------------------------------------------------------------------
# CompactionConfig new fields
# ---------------------------------------------------------------------------


class TestCompactionConfigMicroCompact:
    """Tests for CompactionConfig new fields."""

    def test_default_trigger_is_token_threshold(self):
        config = CompactionConfig()
        assert config.trigger == "token_threshold"
        assert config.gap_threshold_minutes == 5
        assert config.compactable_tools is None

    def test_time_based_trigger_config(self):
        config = CompactionConfig(
            trigger="time_based",
            gap_threshold_minutes=10,
            compaction_strategy="tool_result_compaction",
            compactable_tools=["read_file", "search_file_content"],
        )
        assert config.trigger == "time_based"
        assert config.gap_threshold_minutes == 10
        assert config.compactable_tools == ["read_file", "search_file_content"]

    def test_backward_compat_no_new_fields(self):
        """Config without new fields still works (backward compat)."""
        config = CompactionConfig(
            compaction_strategy="tool_result_compaction",
            keep_iterations=3,
        )
        assert config.trigger == "token_threshold"
        assert config.compactable_tools is None


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


class TestFactoryMicroCompact:
    """Tests for factory functions with new config fields."""

    def test_create_time_based_trigger(self):
        config = CompactionConfig(trigger="time_based", gap_threshold_minutes=7)
        trigger = create_trigger_strategy(config)
        assert isinstance(trigger, TimeBasedTrigger)
        assert trigger.gap_threshold == timedelta(minutes=7)

    def test_create_token_threshold_trigger_default(self):
        config = CompactionConfig()
        trigger = create_trigger_strategy(config)
        from nexau.archs.main_sub.execution.middleware.context_compaction import TokenThresholdTrigger

        assert isinstance(trigger, TokenThresholdTrigger)

    def test_create_tool_result_strategy_with_compactable_tools(self):
        config = CompactionConfig(
            compaction_strategy="tool_result_compaction",
            compactable_tools=["read_file", "run_shell_command"],
        )
        strategy = create_compaction_strategy(config)
        assert isinstance(strategy, ToolResultCompaction)
        assert strategy.compactable_tools == frozenset(["read_file", "run_shell_command"])

    def test_create_tool_result_strategy_without_compactable_tools(self):
        config = CompactionConfig(compaction_strategy="tool_result_compaction")
        strategy = create_compaction_strategy(config)
        assert isinstance(strategy, ToolResultCompaction)
        assert strategy.compactable_tools is None


# ---------------------------------------------------------------------------
# Message.created_at
# ---------------------------------------------------------------------------


class TestMessageCreatedAt:
    """Tests for Message.created_at auto-setting."""

    def test_factory_user_sets_created_at(self):
        msg = Message.user("hello")
        assert msg.created_at is not None

    def test_factory_assistant_sets_created_at(self):
        msg = Message.assistant("hi")
        assert msg.created_at is not None

    def test_explicit_created_at_preserved(self):
        ts = datetime(2025, 1, 1, 12, 0, 0)
        msg = Message(role=Role.USER, content=[TextBlock(text="hi")], created_at=ts)
        assert msg.created_at == ts

    def test_serialization_preserves_created_at(self):
        ts = datetime(2025, 6, 15, 10, 30, 0)
        msg = Message(role=Role.ASSISTANT, content=[TextBlock(text="hi")], created_at=ts)
        data = msg.model_dump()
        restored = Message.model_validate(data)
        # created_at serializes to isoformat string; after round-trip it should parse back
        assert restored.created_at is not None
