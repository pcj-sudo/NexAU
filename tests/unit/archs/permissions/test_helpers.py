# RFC-0019: 权限匹配 helper 函数单元测试

import pytest

from nexau.archs.main_sub.framework_context import FrameworkContext
from nexau.archs.permissions.helpers import (
    check_path_permission,
    check_permission,
    check_shell_permission,
)
from nexau.archs.permissions.types import AskPermission, PermissionDenied


# ---------------------------------------------------------------------------
# check_permission: 通用三态检查
# ---------------------------------------------------------------------------


class TestCheckPermission:
    def test_wildcard_allow(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["**"])
        check_permission(ctx, "anything", "prompt")

    def test_deny_match(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=[], deny_rules=["npm"])
        with pytest.raises(PermissionDenied) as exc_info:
            check_permission(ctx, "npm", "允许执行 npm install 吗?")
        assert exc_info.value.permission_key == "npm"

    def test_allow_match(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["ls", "cat"], deny_rules=[])
        check_permission(ctx, "ls", "允许执行 ls 吗?")

    def test_no_match_raises_ask(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["ls"], deny_rules=["rm"])
        with pytest.raises(AskPermission) as exc_info:
            check_permission(ctx, "npm", "允许执行 npm install 吗?")
        assert exc_info.value.permission_key == "npm"
        assert exc_info.value.prompt == "允许执行 npm install 吗?"

    def test_empty_rules_raises_ask(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=[], deny_rules=[])
        with pytest.raises(AskPermission):
            check_permission(ctx, "any_key", "prompt")

    def test_deny_takes_priority_over_allow(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["npm"], deny_rules=["npm"])
        with pytest.raises(PermissionDenied):
            check_permission(ctx, "npm", "prompt")


# ---------------------------------------------------------------------------
# check_path_permission: 路径专用（gitignore 语义）
# ---------------------------------------------------------------------------


class TestCheckPathPermission:
    def test_wildcard_allow(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["**"])
        check_path_permission(ctx, "/etc/passwd")

    def test_deny_glob_match(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=[], deny_rules=[".env", "~/.ssh/**"])
        with pytest.raises(PermissionDenied):
            check_path_permission(ctx, ".env")

    def test_allow_glob_match(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["/workspace/**"], deny_rules=[])
        check_path_permission(ctx, "/workspace/src/main.py")

    def test_no_match_raises_ask(self) -> None:
        ctx = FrameworkContext.for_testing(
            allow_rules=["/workspace/**"],
            deny_rules=[".env"],
        )
        with pytest.raises(AskPermission) as exc_info:
            check_path_permission(ctx, "/etc/passwd")
        assert exc_info.value.permission_key == "/etc/passwd"

    def test_deny_takes_priority(self) -> None:
        ctx = FrameworkContext.for_testing(
            allow_rules=["/workspace/**"],
            deny_rules=["/workspace/.env"],
        )
        with pytest.raises(PermissionDenied):
            check_path_permission(ctx, "/workspace/.env")

    def test_empty_rules_raises_ask(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=[], deny_rules=[])
        with pytest.raises(AskPermission):
            check_path_permission(ctx, "/any/path")


# ---------------------------------------------------------------------------
# check_shell_permission: 命令专用（shlex 首词匹配）
# ---------------------------------------------------------------------------


class TestCheckShellPermission:
    def test_wildcard_allow(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["**"])
        check_shell_permission(ctx, "rm -rf /")

    def test_deny_first_word(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=[], deny_rules=["rm", "dd"])
        with pytest.raises(PermissionDenied) as exc_info:
            check_shell_permission(ctx, "rm -rf /tmp/test")
        assert exc_info.value.permission_key == "rm"

    def test_allow_first_word(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["ls", "cat"], deny_rules=[])
        check_shell_permission(ctx, "ls -la /workspace")

    def test_no_match_raises_ask(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["ls"], deny_rules=["rm"])
        with pytest.raises(AskPermission) as exc_info:
            check_shell_permission(ctx, "npm install express")
        assert exc_info.value.permission_key == "npm"

    def test_complex_command_parses_first_word(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=["git"], deny_rules=[])
        check_shell_permission(ctx, "git commit -m 'message with spaces'")

    def test_pipe_command_checks_first_word(self) -> None:
        ctx = FrameworkContext.for_testing(allow_rules=[], deny_rules=[])
        with pytest.raises(AskPermission) as exc_info:
            check_shell_permission(ctx, "cat file.txt | grep pattern")
        assert exc_info.value.permission_key == "cat"


# ---------------------------------------------------------------------------
# FrameworkContext.for_tool_call
# ---------------------------------------------------------------------------


class TestFrameworkContextForToolCall:
    def test_creates_independent_context(self) -> None:
        base = FrameworkContext.for_testing(
            session_id="sess_1",
            allow_rules=["**"],
        )
        tool_ctx = base.for_tool_call(
            tool_name="write_file",
            allow_rules=["/workspace/**"],
            deny_rules=[".env"],
        )

        assert tool_ctx.tool_name == "write_file"
        assert tool_ctx.allow_rules == ["/workspace/**"]
        assert tool_ctx.deny_rules == [".env"]
        assert tool_ctx.session_id == "sess_1"
        assert tool_ctx.agent_name == base.agent_name

    def test_does_not_mutate_base(self) -> None:
        base = FrameworkContext.for_testing(allow_rules=["**"])
        base.for_tool_call(
            tool_name="shell",
            allow_rules=["ls"],
            deny_rules=["rm"],
        )
        assert base.allow_rules == ["**"]
        assert base.deny_rules == []

    def test_shares_tools_api(self) -> None:
        base = FrameworkContext.for_testing()
        tool_ctx = base.for_tool_call(
            tool_name="test",
            allow_rules=["**"],
            deny_rules=[],
        )
        assert tool_ctx.tools is not None
        assert tool_ctx.execution is not None
