# Permission matching helpers (reference implementations).
#
# RFC-0019: 工具权限管理
#
# 框架附带的内置 tool 匹配 helper 函数。封装了"匹配规则 + raise 异常"的
# 常见模式。开发者可以直接使用，也可以参考其实现编写自己的判断逻辑。

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

import pathspec

from .types import AskPermission, PermissionDenied

if TYPE_CHECKING:
    from nexau.archs.main_sub.framework_context import FrameworkContext

# RFC-0019: "**" 通配符约定
_WILDCARD = "**"


def check_permission(
    ctx: FrameworkContext,
    permission_key: str,
    prompt: str,
) -> None:
    """通用三态检查（参考实现）。

    RFC-0019: 内置 tool 的匹配 helper

    对 permission_key 与 allow/deny rules 做精确匹配：
    命中 allow → 返回、命中 deny → raise PermissionDenied、无命中 → raise AskPermission。
    """
    # 1. "**" 通配符 = 无条件放行
    if _WILDCARD in ctx.allow_rules:
        return

    # 2. deny 优先
    if permission_key in ctx.deny_rules:
        raise PermissionDenied(
            reason=f"{permission_key} 被禁止",
            permission_key=permission_key,
        )

    # 3. allow 精确匹配
    if permission_key in ctx.allow_rules:
        return

    # 4. 无命中 → ask
    raise AskPermission(prompt=prompt, permission_key=permission_key)


def check_path_permission(ctx: FrameworkContext, path: str) -> None:
    """路径专用三态检查。

    RFC-0019: 内置 filesystem helper

    使用 pathspec 库（gitignore 语义）做模式匹配。
    供 read_file / write_file / edit_file 使用。
    """
    # 1. "**" 通配符 = 无条件放行
    if _WILDCARD in ctx.allow_rules:
        return

    # 2. deny 匹配（gitignore 语义）
    if ctx.deny_rules:
        deny_spec = pathspec.PathSpec.from_lines("gitwildmatch", ctx.deny_rules)
        if deny_spec.match_file(path):
            raise PermissionDenied(
                reason=f"路径 {path} 被禁止",
                permission_key=path,
            )

    # 3. allow 匹配（gitignore 语义）
    if ctx.allow_rules:
        allow_spec = pathspec.PathSpec.from_lines("gitwildmatch", ctx.allow_rules)
        if allow_spec.match_file(path):
            return

    # 4. 无命中 → ask
    raise AskPermission(
        prompt=f"允许访问 {path} 吗?",
        permission_key=path,
    )


def check_shell_permission(ctx: FrameworkContext, command: str) -> None:
    """命令专用三态检查。

    RFC-0019: 内置 shell helper

    使用 shlex 解析出首词做精确匹配。
    供 run_shell_command 使用。
    """
    # 1. "**" 通配符 = 无条件放行
    if _WILDCARD in ctx.allow_rules:
        return

    # 2. 解析首词
    try:
        head = shlex.split(command)[0]
    except ValueError:
        head = command.split()[0] if command.split() else command

    # 3. deny 匹配
    if head in ctx.deny_rules:
        raise PermissionDenied(
            reason=f"命令 {head} 被禁止",
            permission_key=head,
        )

    # 4. allow 匹配
    if head in ctx.allow_rules:
        return

    # 5. 无命中 → ask
    raise AskPermission(
        prompt=f"允许执行 {command} 吗?",
        permission_key=head,
    )
