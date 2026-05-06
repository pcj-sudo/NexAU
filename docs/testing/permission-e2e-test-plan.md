# 工具权限管理端到端测试计划

> 对应实现：RFC-0019 Tool Permission Management

## 测试目标

验证 NexAU 所有内置工具在权限管理框架下的行为与 Claude Code 对齐：
- 只读工具无权限检查，自动放行
- 写入/执行类工具按规则三态判定（allow / ask / deny）
- Shell 只读命令白名单自动放行
- 域名级 WebFetch 权限控制
- 完整 ask → resolve → resume 生命周期

## 测试环境准备

### 方式 A：CC Agent + E2B 沙箱（推荐，覆盖全部 15 个工具含 run_code_tool）

```bash
cd /path/to/NexAU
export E2B_API_URL="https://hk-prod-e2b.xiaobei.top"
export E2B_API_KEY="your_key"
export E2B_DOMAIN="hk-prod-e2b.xiaobei.top"
HTTP_PROXY="" uv run python scripts/demo_cc_agent.py
```

脚本源码：`scripts/demo_cc_agent.py`
Agent 定义：`examples/cc_agent/`

### 方式 B：本地工作区（无 E2B，跳过 run_code_tool）

脚本会自动创建测试工作区 `/tmp/nexau_perm_test/workspace`（含 `src/main.py`、`.env`、`data.txt`）。

```bash
cd /path/to/NexAU
HTTP_PROXY="" uv run python scripts/demo_permission_full.py
```

脚本源码：`scripts/demo_permission_full.py`

### 权限规则配置

| 工具 | permissions 配置 | 预期行为 |
|------|-----------------|---------|
| read_file | `None`（无配置） | 自动放行 |
| write_file | `{allow: [], deny: [".env", "~/.ssh/**", "*.pem", "*.key"]}` | 敏感文件拒绝，其余 ask |
| replace | 同 write_file | 同上 |
| apply_patch | 同 write_file | 同上 |
| list_directory | `None` | 自动放行 |
| search_file_content | `None` | 自动放行 |
| read_many_files | `None` | 自动放行 |
| run_shell_command | `{allow: [], deny: ["rm", "sudo", "chmod", "chown", "dd"]}` | 只读命令白名单放行，deny 拒绝，其余 ask |
| run_code_tool | `{allow: [], deny: []}` | 每次 ask（需要 E2B 沙箱） |
| web_fetch | `{allow: [], deny: []}` | 每次 ask |
| google_web_search | `None` | 自动放行 |

---

## 测试用例

### Phase 1：只读工具 — 验证自动放行

**目标**：无 `permissions` 配置的工具，`allow_rules=["**"]`，一切自动放行，不弹窗。

#### T1.1 read_file

**操作**：
```
You: 读取 /tmp/nexau_perm_test/workspace/src/main.py
```

**验证**：
- [ ] Agent 直接返回文件内容 `hello world`
- [ ] 无权限弹窗
- [ ] Langfuse trace 中 `Tool: read_file` span 正常完成

#### T1.2 glob

**操作**：
```
You: 搜索 /tmp/nexau_perm_test/workspace 下所有 .py 文件
```

**验证**：
- [ ] 返回 `src/main.py`
- [ ] 无权限弹窗

#### T1.3 list_directory

**操作**：
```
You: 列出 /tmp/nexau_perm_test/workspace 目录内容
```

**验证**：
- [ ] 返回目录列表（src/、.env、data.txt）
- [ ] 无权限弹窗

#### T1.4 search_file_content

**操作**：
```
You: 在 /tmp/nexau_perm_test/workspace 中搜索包含 "hello" 的文件
```

**验证**：
- [ ] 找到 `src/main.py` 中的匹配
- [ ] 无权限弹窗

#### T1.5 google_web_search

**操作**：
```
You: 搜索 "Python asyncio tutorial"
```

**验证**：
- [ ] 返回搜索结果（或 API key 缺失的合理错误）
- [ ] 无权限弹窗

---

### Phase 2：文件写入工具 — 验证路径级 allow / ask / deny

**目标**：`check_path_permission` 按 gitignore 语义匹配路径。

#### T2.1 write_file — allow 路径

**操作**：
```
You: 创建文件 /tmp/nexau_perm_test/workspace/src/utils.py，内容为 "def hello(): pass"
```

**验证**：
- [ ] 自动放行（`/tmp/nexau_perm_test/workspace/src/**` 匹配）
- [ ] 文件创建成功
- [ ] 无权限弹窗

#### T2.2 write_file — deny 路径

**操作**：
```
You: 修改 /tmp/nexau_perm_test/workspace/.env 文件，写入 "SECRET=hacked"
```

**验证**：
- [ ] **立即拒绝**，不弹窗
- [ ] Agent 回复中包含"禁止"/"denied"/"permission"等字样
- [ ] .env 文件内容未改变

#### T2.3 write_file — ask 路径

**操作**：
```
You: 创建文件 /tmp/nexau_perm_test/workspace/README.md，内容为 "# Test"
```

**验证**：
- [ ] 弹出权限请求：`允许访问 /tmp/nexau_perm_test/workspace/README.md 吗?`
- [ ] 输入 `allow` → 文件创建成功
- [ ] 输入 `deny` → 文件未创建，Agent 报告被拒绝

#### T2.4 replace — allow 路径

**操作**：
```
You: 把 /tmp/nexau_perm_test/workspace/src/main.py 中的 "hello" 替换为 "hi"
```

**验证**：
- [ ] 自动放行
- [ ] 文件内容变为 "hi world"

#### T2.5 replace — deny 路径

**操作**：
```
You: 把 /tmp/nexau_perm_test/workspace/.env 中的 "abc123" 替换为 "newpass"
```

**验证**：
- [ ] 立即拒绝
- [ ] .env 内容未改变

#### T2.6 apply_patch — 混合路径

**操作**：
```
You: 用 patch 同时修改 src/main.py（改 "hi" 为 "hey"）和 .env（改 SECRET 值）
```

**验证**：
- [ ] src/main.py 的 hunk 自动放行
- [ ] .env 的 hunk 被拒绝
- [ ] Agent 报告部分成功、部分拒绝

---

### Phase 3：Shell 命令 — 验证只读白名单 + allow / deny

**目标**：只读命令白名单自动放行，deny 命令立即拒绝，其余 ask。

#### T3.1 只读命令 — 自动放行

**操作**：
```
You: 执行命令 ls -la /tmp/nexau_perm_test/workspace
```

**验证**：
- [ ] 自动放行（`ls` 在只读白名单中）
- [ ] 返回目录列表
- [ ] 无权限弹窗

#### T3.2 只读命令 — cat

**操作**：
```
You: 执行命令 cat /tmp/nexau_perm_test/workspace/data.txt
```

**验证**：
- [ ] 自动放行（`cat` 在只读白名单中）
- [ ] 返回文件内容

#### T3.3 只读命令 — git log

**操作**：
```
You: 在 /Users/pcj/coding_dev/NexAU 执行 git log --oneline -5
```

**验证**：
- [ ] 自动放行（`git` + `log` 在只读白名单中）
- [ ] 返回最近 5 条 commit

#### T3.4 git 写入子命令 — ask

**操作**：
```
You: 执行 git commit -m "test"
```

**验证**：
- [ ] 弹出权限请求（`git` + `commit` 不在只读白名单中）
- [ ] 输入 `deny` → 命令未执行

#### T3.5 allow 规则 — python

**操作**：
```
You: 执行命令 python --version
```

**验证**：
- [ ] 自动放行（`python` 在 allow 规则中）
- [ ] 返回 Python 版本号

#### T3.6 deny 规则 — rm

**操作**：
```
You: 执行命令 rm /tmp/nexau_perm_test/workspace/data.txt
```

**验证**：
- [ ] **立即拒绝**
- [ ] data.txt 文件仍然存在
- [ ] Agent 报告命令被禁止

#### T3.7 deny 规则 — sudo

**操作**：
```
You: 执行命令 sudo ls /root
```

**验证**：
- [ ] 立即拒绝
- [ ] Agent 报告命令被禁止

#### T3.8 未知命令 — ask

**操作**：
```
You: 执行命令 curl https://example.com
```

**验证**：
- [ ] 弹出权限请求：`允许执行 curl https://example.com 吗?`
- [ ] 输入 `allow` → 命令执行
- [ ] 输入 `deny` → 命令未执行

---

### Phase 4：Web Fetch — 验证域名级权限

**目标**：`check_url_permission` 按域名匹配，支持通配符。

#### T4.1 allow 域名 — github.com

**操作**：
```
You: 抓取 https://github.com/anthropics/claude-code 页面内容
```

**验证**：
- [ ] 自动放行（`github.com` 在 allow 规则中）
- [ ] 返回页面内容

#### T4.2 allow 通配域名 — *.github.com

**操作**：
```
You: 抓取 https://api.github.com/repos/anthropics/claude-code
```

**验证**：
- [ ] 自动放行（`*.github.com` 匹配 `api.github.com`）
- [ ] 返回 API 响应

#### T4.3 deny 域名 — evil.com

**操作**：
```
You: 抓取 https://evil.com/malware
```

**验证**：
- [ ] 立即拒绝
- [ ] 无网络请求发出
- [ ] Agent 报告域名被禁止

#### T4.4 未知域名 — ask

**操作**：
```
You: 抓取 https://example.com
```

**验证**：
- [ ] 弹出权限请求：`允许访问 https://example.com 吗?`
- [ ] 输入 `allow` → 抓取成功
- [ ] 输入 `deny` → 未抓取

---

### Phase 5：代码执行工具 — 验证 ask 行为（需要 E2B）

**目标**：`run_code_tool` 配置 `{allow: [], deny: []}` → 每次执行都 ask。

#### T5.1 run_code_tool — ask

**操作**：
```
You: 用 run_code_tool 执行 print(1+1)
```

**验证**：
- [ ] 弹出权限请求：`允许执行代码吗?`
- [ ] 输入 `allow` → 执行成功，返回 `2`
- [ ] Langfuse trace 中 `Tool: run_code_tool` span 正常

#### T5.2 run_code_tool — deny

**操作**：
```
You: 用 run_code_tool 执行 import os; print(os.listdir('/'))
```

**验证**：
- [ ] 弹出权限请求
- [ ] 输入 `deny` → 代码未执行
- [ ] Agent 报告被拒绝

#### T5.3 run_code_tool — allow 后持久化

**操作**（接 T5.1，假设用了 `allow`）：
```
You: 再用 run_code_tool 执行 print('hello')
```

**验证**：
- [ ] **自动放行**（上次 allow 写入了 `code_execution` 到 DB）
- [ ] 返回 `hello`
- [ ] 无权限弹窗

---

### Phase 6：持久化规则验证

**目标**：`allow` 决策写入 DB 后，同 permission_key 后续自动放行。

#### T6.1 allow_once 不持久化

**操作**（接 Phase 4 T4.4，假设用了 `allow_once`）：
```
You: 再次抓取 https://example.com
```

**验证**：
- [ ] **再次弹窗**（`allow_once` 不写入 DB）

#### T6.2 allow 持久化

**操作**（接 Phase 3 T3.8，假设用了 `allow`）：
```
You: 再次执行 curl https://example.com
```

**验证**：
- [ ] **自动放行**（上次 allow 写入了 `curl` 到 DB）
- [ ] 无权限弹窗

---

### Phase 7：混合并行工具调用

**目标**：一轮内多个工具同时调用，各自独立判定。

#### T7.1 三工具并行（allow + ask + deny）

**操作**：
```
You: 同时做三件事：
1. 读取 /tmp/nexau_perm_test/workspace/src/main.py
2. 创建 /tmp/nexau_perm_test/workspace/config.yaml 内容 "key: value"
3. 执行命令 rm /tmp/nexau_perm_test/workspace/data.txt
```

**验证**：
- [ ] read_file → 自动放行，返回文件内容
- [ ] write_file → 弹窗 ask（config.yaml 不在 allow 路径中）
- [ ] run_shell_command → 立即拒绝（`rm` 在 deny 规则中）
- [ ] Agent 报告：读取成功、写入待授权、删除被拒绝

#### T7.2 resolve 后 resume

**操作**：
```
（接上一步的权限弹窗）
→ allow / allow_once / deny: allow
```

**验证**：
- [ ] write_file 执行成功，config.yaml 被创建
- [ ] Agent 汇总所有结果

---

### Phase 8：Session 工具 — 验证自动放行

**目标**：会话级工具无权限检查。

#### T8.1 write_todos

**操作**：
```
You: 创建待办事项：1. 写单测 2. 代码审查
```

**验证**：
- [ ] 自动放行
- [ ] 待办列表创建成功

#### T8.2 save_memory

**操作**：
```
You: 记住：这个项目使用 Python 3.12
```

**验证**：
- [ ] 自动放行
- [ ] 记忆保存成功

---

## 观测方式

### 1. Langfuse 面板

打开 https://langfuse.xiaobei.top，搜索 trace name = `permission_full_test`。

每个 trace 中验证：
- **LLM Generation**：查看 system prompt、user message、tool_calls
- **Tool Span**：查看每个工具的输入参数、执行结果、耗时
- **Permission Span**（如有）：查看权限判定结果

### 2. 终端日志

```bash
# INFO 级别：看权限判定 + 工具执行
HTTP_PROXY="" uv run python scripts/demo_permission_full.py 2>&1 | grep -E "✅|❌|⚠|Permission|AskPermission|PermissionDenied"

# DEBUG 级别：看完整 LLM 请求/响应
HTTP_PROXY="" uv run python scripts/demo_permission_full.py --log-level=DEBUG
```

### 3. 文件系统验证

每个写入/删除操作后，检查文件是否真的被修改：
```bash
cat /tmp/nexau_perm_test/workspace/src/main.py
cat /tmp/nexau_perm_test/workspace/.env
ls /tmp/nexau_perm_test/workspace/
```

### 4. DB 验证

测试结束后检查持久化规则：
```python
rules = await sm.load_permission_rules(user_id, session_id, "run_shell_command")
print(rules)  # 应包含用户 allow 过的命令
```

---

## 通过标准

| 标准 | 要求 |
|------|------|
| Phase 1 全部 | 所有只读工具无弹窗 |
| Phase 2 全部 | 路径 allow/ask/deny 三态正确 |
| Phase 3 全部 | 只读白名单放行 + deny 拒绝 + 未知 ask |
| Phase 4 全部 | 域名 allow/deny/ask 三态正确 |
| Phase 5 全部 | 代码执行 ask + allow 持久化（需 E2B） |
| Phase 6 全部 | allow 持久化、allow_once 不持久化 |
| Phase 7 全部 | 并行混合判定 + resume 正确 |
| Phase 8 全部 | 会话工具无弹窗 |
| 无回归 | 整个过程中无未预期的异常或崩溃 |
| Langfuse | 所有 trace 可见，无丢失 |

---

## 附录：测试脚本说明

### `scripts/demo_cc_agent.py`（推荐）

CC 对齐 agent 的完整交互式测试脚本，使用 E2B 沙箱。

- 注册全部 15 个内置工具（含 run_code_tool）
- CC 对齐权限：只读自动放行、写入 ask（敏感文件 deny）、shell 只读白名单、代码执行 ask、域名 ask
- 需要 E2B 环境变量：`E2B_API_URL`、`E2B_API_KEY`、`E2B_DOMAIN`
- Agent 定义：`examples/cc_agent/`
- Langfuse trace name: `cc_agent_permission_test`

### `scripts/demo_permission_full.py`

本地工作区版测试脚本，不需要 E2B（但不含 run_code_tool）。

- 注册 10 个内置工具（不含 run_code_tool、ask_user 等会话工具）
- 自动创建测试工作区 `/tmp/nexau_perm_test/workspace`
- Langfuse trace name: `permission_full_test`
