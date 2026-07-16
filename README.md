# ProofLoom

ProofLoom 是一个本地优先、证据驱动的 AI 学习工作台。它把可信的 Markdown 解析成可定位的 Source Fragment（来源片段），让 LLM 或离线 fixture 只提出 Candidate Assertion（候选断言），再由人逐条审核。Assertion Ledger（断言账本）及其 Review Event（审核事件）是权威记录；Query Graph（查询图）只是面向浏览的投影。

v0.1 完整交付 Build 流程和轻量 Explore 流程。它不是聊天机器人，也不会自动把 LLM 输出当成事实。只有当前有效且已接受的断言才会进入 Query Graph。

## 先了解运行边界

- Web UI 只绑定 `127.0.0.1` 或 `localhost` 等本机回环地址，不是云端网站，也没有登录系统。
- 在界面中“导入 Markdown”是服务器进程从 `browse_root` 下读取本地文件，不是上传到云端；源文件不会被复制进仓库。
- `browse_root` 是 UI 可选择项目和来源的路径边界。默认示例配置把它设为配置文件所在目录（仓库根目录）。
- Knowledge Project 的受管数据写入项目目录下的 `.proofloom/`；创建项目时，ProofLoom 会把 `.proofloom/` 加入该项目自己的 `.gitignore`。
- 离线 fixture 不访问网络。Codex CLI 和 OpenAI-compatible 模式会把 Entity Dictionary 与 Source Fragment 内容发给所配置的外部模型服务，请只导入允许发送给该服务的资料。
- `proofloom.local.json` 可能含明文 API key，已被仓库根 `.gitignore` 忽略，但仍应限制本机文件访问权限，且绝不能提交、粘贴到 Issue 或截图公开。

## 环境要求

- Python 3.11 或更高版本
- 本地浏览器
- 仅在使用 Codex 模式时：Codex CLI stable `0.144.5` 或更高版本，以及已保存的 ChatGPT 登录

合成示例可以完全离线运行，不需要 API key。

## Windows PowerShell：安装并启动

以下命令从仓库根目录执行，可以直接复制。首次建议先使用默认的 Codex 配置；如果只想跑离线示例，即使没有 Codex 登录，也可以启动后选择 fixture。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
Copy-Item .\proofloom.example.json .\proofloom.local.json
codex --version
codex login status
.\.venv\Scripts\proofloom.exe --config .\proofloom.local.json
```

`codex --version` 必须显示 stable `0.144.5` 或更新版本，`codex login status` 应确认已经通过 ChatGPT 登录。若使用 npm 安装且版本过旧，可先运行：

```powershell
npm install -g @openai/codex@latest
codex --version
codex login
codex login status
```

启动成功后打开 <http://127.0.0.1:8000>。保持终端窗口运行；结束服务按 `Ctrl+C`。

不使用 JSON 配置时，仍可直接启动：

```powershell
.\.venv\Scripts\python.exe -m proofloom.app --browse-root .
```

开发时如需可编辑安装，把安装命令改为 `.\.venv\Scripts\python.exe -m pip install -e .`。

## macOS / Linux（POSIX shell）

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
cp ./proofloom.example.json ./proofloom.local.json
codex --version
codex login status
.venv/bin/proofloom --config ./proofloom.local.json
```

无配置文件的等价启动命令是：

```sh
.venv/bin/python -m proofloom.app --browse-root .
```

打开 <http://127.0.0.1:8000>，结束服务按 `Ctrl+C`。

## 本地 JSON 配置

`proofloom.example.json` 是可提交的无密钥模板。把它复制为被 `.gitignore` 忽略的 `proofloom.local.json`，再用 `--config` 启动。相对的 `browse_root` 以配置文件所在目录为基准；`host` 只能是本机回环地址。

### 复用 Codex 已保存的 ChatGPT 登录

默认模板如下。ProofLoom 不读取 API key，而是调用本机已登录的 Codex CLI；当前推荐模型是 `gpt-5.6-luna`，推理强度为 `medium`。

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000,
    "browse_root": "."
  },
  "llm": {
    "backend": "codex-cli",
    "model": "gpt-5.6-luna",
    "reasoning": "medium",
    "timeout": 120
  }
}
```

启动前检查：

```powershell
codex --version
codex login status
.\.venv\Scripts\proofloom.exe --config .\proofloom.local.json
```

Codex 抽取在独立临时目录中执行，关闭模型侧 Web 搜索、网络工具、登录 shell 和环境变量继承；但 Codex CLI 控制进程仍需联网访问模型服务，并会把本次抽取所需的 Entity Dictionary 和 Source Fragment 放入模型请求。登录复用不代表数据留在本机。

### OpenAI-compatible API

把 `llm` 改为下面的形式。`endpoint` 是完整的 Chat Completions URL；`api_key`、模型名和服务商标签请替换为自己的值。

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8000,
    "browse_root": "."
  },
  "llm": {
    "backend": "openai-compatible",
    "api_key": "REPLACE_WITH_YOUR_LOCAL_KEY",
    "model": "configured-model",
    "endpoint": "https://api.example.com/v1/chat/completions",
    "provider": "configured-provider",
    "timeout": 30
  }
}
```

也可以用 `base_url` 代替 `endpoint`，ProofLoom 会追加 `/chat/completions`：

```json
{
  "llm": {
    "backend": "openai-compatible",
    "api_key": "REPLACE_WITH_YOUR_LOCAL_KEY",
    "model": "configured-model",
    "base_url": "https://api.example.com/v1",
    "provider": "configured-provider",
    "timeout": 30
  }
}
```

`endpoint` 与 `base_url` 互斥，不能同时填写。远程服务必须使用 HTTPS；明文 HTTP 只允许 `localhost` 或其他回环地址。API key 只进入 Authorization 请求头，不写入 Knowledge Project，也不显示在 Web UI 中；但它会以明文保存在本地 JSON 文件里。

旧的环境变量配置仍可在未给出 JSON `llm` 时使用：`PROOFLOOM_OPENAI_API_KEY`、`PROOFLOOM_OPENAI_MODEL`、`PROOFLOOM_OPENAI_ENDPOINT`（或 `PROOFLOOM_OPENAI_BASE_URL`）和 `PROOFLOOM_OPENAI_PROVIDER`。

## 从零跑通双文档 Web UI 示例

仓库提供两篇原创合成文档：`examples/synthetic-workflow/inspection.md` 和 `examples/synthetic-workflow/safety.md`。下面的步骤不会使用或分发第三方教程原始资料。

### 1. 准备并启动

在仓库根目录创建空项目目录，然后按前文启动服务：

```powershell
New-Item -ItemType Directory -Force .\demo-project
.\.venv\Scripts\proofloom.exe --config .\proofloom.local.json
```

打开 <http://127.0.0.1:8000>。

### 2. 创建或打开 Knowledge Project

在 **Create a Knowledge Project** 中：

1. 填写项目名，例如 `Synthetic Demo`。
2. 选择仓库下的 `demo-project` 目录。
3. 点击 **Create project**。

下次启动后，使用 **Open a Knowledge Project** 选择同一目录即可恢复项目。

### 3. 从本地目录导入 Markdown

点击 **Import Markdown**，选择仓库下的 `examples/synthetic-workflow` 目录，再点击 **Import selected source**。这里是从本机目录读取，不是浏览器向云端上传。项目页应显示来自 `inspection.md` 和 `safety.md` 的可定位 Source Fragment。

### 4. 维护受控 Entity Dictionary

打开 **Review Entity Dictionary**。对下表四个名称逐个执行：在 **Submit an unknown name** 中提交名称，再从候选实体选择精确类型并点击 **Accept entity**。

| Name | Type |
| --- | --- |
| Inspector | Component |
| Inspection Report | Artifact |
| Safety Gate | Component |
| Risky Command | Artifact |

v0.1 只允许四种受控 Entity 类型：`Component`、`Artifact`、`Pattern`、`Concept`。未知名称只能先成为候选，不会自动进入已接受词典。

### 5. 生成并校验 Candidate Assertion

打开 **Extract Candidate Assertions**，选择一种方式：

- 点击 **Run offline synthetic fixture extraction**：完全离线、结果可复现，推荐第一次体验使用。
- 点击 **Run configured LLM extraction**：使用 `proofloom.local.json` 里的 Codex CLI 或 OpenAI-compatible 配置。

fixture 路径应产生两条候选，并在 **Validation output** 中显示 `valid`。LLM 输出可能不同；校验失败的候选会显示字段级原因，不能靠重试绕过契约。

当前 predicate 范围及类型方向为：

| Predicate | Subject → Object |
| --- | --- |
| `COMPOSED_OF` | `Concept → Component` |
| `PROMPTS` | `Artifact → Component` |
| `CALLS_TOOL` | `Component → Component` |
| `PRODUCES` | `Component → Artifact` |
| `VERIFIES` | `Component → Artifact` |
| `BLOCKS` | `Component → Artifact` |

每条候选必须引用已接受的实体，并至少有一个可定位的主要 Evidence Reference。

### 6. 逐条人工审核

在 Candidate Assertion 卡片中对照 subject、predicate、object、来源文件、标题路径和原文片段，再选择：

- `accept`（界面 **Accept**）：接受当前候选。
- `reject`（界面 **Reject**）：拒绝候选。
- `replace`（界面 **Replace**）：填写修正后的 subject、predicate、object；原候选保留并记录替换关系。
- `needs_domain_review`（界面 **Needs domain review**）：暂时无法判断，留待领域复核。

这些操作追加不可变 Review Event，不会静默覆盖 LLM 的原始提案。要完成合成演示，可以确认两条候选证据后都选择 **Accept**。未审核、被拒绝、被替换、`needs_domain_review` 或证据已失效的断言都不会进入 Query Graph。

### 7. 生成并浏览 Query Graph

返回项目页点击 **Project and explore graph**。在 **Graph Explorer** 中可以按实体类型或关系类型过滤；点击边的 **Trace evidence**，通过 `assertion_id` 回到 Assertion Ledger，检查审核状态、来源文件、标题路径和原文。

重新导入已修改的来源后，旧片段会标为 `changed`，引用已变更或缺失证据的已接受断言会变为 `stale`，并在下一次投影时撤出 Query Graph。历史断言和 Review Event 仍保留。

### 8. 运行发布完整性检查

先在 UI 中重新投影图，再开一个 PowerShell 终端执行：

```powershell
.\.venv\Scripts\proofloom.exe check demo-project
```

模块形式也可用：

```powershell
.\.venv\Scripts\python.exe -m proofloom.app check demo-project
```

POSIX：

```sh
.venv/bin/proofloom check demo-project
.venv/bin/python -m proofloom.app check demo-project
```

检查会逐边验证 `assertion_id` 在 Assertion Ledger 中唯一存在、当前审核状态是 accepted、Schema/词典/predicate/证据仍有效、图边与断言一致，并确保每个证据引用都能回到当前 Source Fragment。失败时命令返回非零状态并指出问题边。

## 常见问题

### 页面打不开或端口被占用

确认启动终端仍在运行。可以修改本地 JSON 的 `server.port`，或用命令行临时覆盖：

```powershell
.\.venv\Scripts\proofloom.exe --config .\proofloom.local.json --port 8765
```

然后打开 `http://127.0.0.1:8765`。ProofLoom 拒绝绑定 `0.0.0.0` 或公网地址；本地 UI 没有身份认证，不应对外暴露。

### 找不到项目或 Markdown

项目和来源必须位于 `browse_root` 内。相对 `browse_root` 以 JSON 配置文件目录解析；需要访问其他本地目录时，把 `browse_root` 改为它们共同的可信父目录，然后重启。

### Codex CLI 找不到、版本不兼容或未登录

```powershell
where.exe codex
codex --version
codex login status
```

Windows 的 Codex 抽取需要可在 `PATH` 中发现的原生 npm Codex 安装，且 stable 版本至少为 `0.144.5`。缺失或过旧时执行 `npm install -g @openai/codex@latest`，重新打开终端，再运行 `codex login`。ProofLoom 复用保存的 ChatGPT 登录，不会从 JSON 读取 Codex API key。

### 配置文件启动失败

- JSON 必须是 UTF-8 对象，未知字段会被拒绝。
- `server.host` 只能是 `localhost` 或回环 IP；端口范围是 0–65535。
- `server.browse_root` 必须是已存在的目录。
- Codex 的 `model`、`reasoning` 必须非空，`timeout` 必须是正数。
- OpenAI-compatible 的 `api_key`、`model`、URL 和 `provider` 必须非空；`endpoint` 和 `base_url` 只能选一个。
- 错误信息不会打印 API key；排障时也不要把完整本地配置公开。

### 候选无效或图为空

先检查 Entity Dictionary 是否精确接受了主体和客体，再查看 **Validation output** 的字段级原因。只有验证通过、人工接受、证据当前有效且未被替换的断言才能投影；审核后需再次点击 **Project and explore graph**。

### 发布完整性检查提示失败

先在 UI 中重新投影。如果来源已修改，应重新导入、重新抽取并人工复核；不要手改 `.proofloom/` 下的 JSON 来绕过 Assertion Ledger 或 Review Event。

## 开发与发布前检查

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q src tests
git check-ignore -v proofloom.local.json
git status --short
```

`git check-ignore` 应显示根 `.gitignore` 的规则；`git status` 不应列出 `proofloom.local.json`、用户 Knowledge Project 数据、API key 或第三方教程原始资料。Schema 和离线 fixture 已在 `pyproject.toml` 中声明为包数据，因此安装到仓库外也可使用。

## v0.1 范围与信任模型

ProofLoom v0.1 不包含身份认证、云端托管、多用户角色、非 Markdown 导入、Neo4j 强制依赖、通用插件系统、对话式问答、出题/评分/复习 Agent，也不会自动接受 LLM 断言。代码、Schema、提示词和原创/合成示例可以发布；用户本地项目数据、密钥以及未经确认可再分发的第三方教程资料不得随仓库发布。

ProofLoom 仍是工作名称。正式公开发布前，项目所有者仍需确认许可证，并复核名称、仓库、软件包和商标可用性。
