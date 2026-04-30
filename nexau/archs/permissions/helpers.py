# Permission matching helpers (reference implementations).
#
# RFC-0019: 工具权限管理
#
# 框架附带的内置 tool 匹配 helper 函数。封装了"匹配规则 + raise 异常"的
# 常见模式。开发者可以直接使用，也可以参考其实现编写自己的判断逻辑。

from __future__ import annotations

import fnmatch
import shlex
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import pathspec

from .types import AskPermission, PermissionDenied

if TYPE_CHECKING:
    from nexau.archs.main_sub.framework_context import FrameworkContext

# RFC-0019: "**" 通配符约定
_WILDCARD = "**"

# CC 对齐: Bash 只读命令白名单，这些命令无论规则如何都自动放行
_READONLY_COMMANDS: frozenset[str] = frozenset({
    "ls", "cat", "head", "tail", "find", "wc", "diff", "stat",
    "du", "file", "which", "whereis", "whoami", "pwd", "echo",
    "env", "printenv", "date", "uname", "hostname", "id",
    "basename", "dirname", "realpath", "readlink", "md5sum",
    "sha256sum", "sort", "uniq", "tr", "cut", "awk", "sed",
    "grep", "egrep", "fgrep", "rg", "ag", "less", "more",
    "tree", "type", "man", "help",
})

# CC 对齐: git 只读子命令白名单
_READONLY_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    "log", "status", "diff", "show", "branch", "tag", "remote",
    "config", "describe", "rev-parse", "rev-list", "shortlog",
    "blame", "ls-files", "ls-tree", "ls-remote", "cat-file",
    "name-rev", "reflog",
})


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
    CC 对齐: 只读命令（ls, cat, git log 等）无条件放行。
    供 run_shell_command 使用。
    """
    # 1. "**" 通配符 = 无条件放行
    if _WILDCARD in ctx.allow_rules:
        return

    # 2. 解析首词
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    head = tokens[0] if tokens else command

    # 3. CC 对齐: 只读命令白名单 — 无条件放行（deny 规则仍可覆盖）
    if head in ctx.deny_rules:
        raise PermissionDenied(
            reason=f"命令 {head} 被禁止",
            permission_key=head,
        )
    if head in _READONLY_COMMANDS:
        return
    if head == "git" and len(tokens) > 1 and tokens[1] in _READONLY_GIT_SUBCOMMANDS:
        return

    # 4. allow 匹配
    if head in ctx.allow_rules:
        return

    # 5. 无命中 → ask
    raise AskPermission(
        prompt=f"允许执行 {command} 吗?",
        permission_key=head,
    )


def check_url_permission(ctx: FrameworkContext, url: str) -> None:
    """域名级三态检查。

    CC 对齐: WebFetch 按域名控制

    从 URL 中提取 hostname，与 allow/deny 规则做匹配。
    deny/allow 规则支持 fnmatch 通配（如 ``*.github.com``）。
    供 web_fetch 使用。
    """
    # 1. "**" 通配符 = 无条件放行
    if _WILDCARD in ctx.allow_rules:
        return

    # 2. 提取域名
    hostname = urlparse(url).hostname or url

    # 3. deny 匹配（支持 *.example.com 通配）
    for pattern in ctx.deny_rules:
        if fnmatch.fnmatch(hostname, pattern):
            raise PermissionDenied(
                reason=f"域名 {hostname} 被禁止",
                permission_key=hostname,
            )

    # 4. allow 匹配
    for pattern in ctx.allow_rules:
        if fnmatch.fnmatch(hostname, pattern):
            return

    # 5. 无命中 → ask
    raise AskPermission(
        prompt=f"允许访问 {url} 吗?",
        permission_key=hostname,
    )
