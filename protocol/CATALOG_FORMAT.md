# Agent Jarvis 广场包格式 v1（agentjarvis.catalog/v1）

> 给平台侧（vibe-remote 广场后端 / 安装器）对齐用。本目录 `skills/*`、`workflows/*` 每个子目录就是一个可直接导入的包。
> English summary: every package is a self-contained folder with a `manifest.json` at its root. The platform imports the folder as-is, verifies `files[].sha256`, and installs it into the harness skill directory (skill) or scaffolds it into a new workspace sub-folder (workflow).

## 1. 目录约定

```
skills/<name>/                     # type = skill
  manifest.json                    # 必有，见 §2
  SKILL.md                         # 必有，agent 读的入口（frontmatter: name / description）
  README.md                        # 必有，人读：中文说明 + English summary + 安装 + 自备清单
  scripts/  或 cli/                 # 可选，确定性 CLI；必须自定位，不引用包外绝对路径
  requirements.txt                 # 有 Python 三方依赖时必有
  .env.example                     # 需要凭据时必有：只写变量名和获取方式，绝不写值
  references/ · tests/             # 可选
  LICENSE · NOTICE.md              # 上游第三方内容必须保留

workflows/<name>/                  # type = workflow
  manifest.json
  README.md                        # 中文说明 + English summary + 安装 + 自备清单 + 首次运行
  params.schema.json               # 安装向导要问的参数（品牌名、平台、语言……），JSON Schema draft-07
  scaffold/                        # 安装后整体复制成 <工作区>/<工作流目录>/，按 Workflow Design Bible 结构
    CLAUDE.md · AGENTS.md          # 薄路由（两个 harness 的孪生入口）
    documentation/                 # CONSTITUTION / IDENTITY / SOUL / WORKFLOW / ROLES / MEMORY / NEXT_SESSION / CHANGELOG / STRUCTURE.json
    .claude/agents/*.md            # 角色定义（subagent prompt）
    .claude/skills/<本工作流专属 skill>/   # 可选；通用 skill 不复制，写进 manifest.requires.skills 由安装器从广场拉
```

- `scaffold/` 里的占位符统一写 `{{param_name}}`，名字必须在 `params.schema.json` 里有定义；安装器渲染后再落盘。
- 包内一切路径都是**包相对**或 `$HOME`/环境变量；脚本用 `Path(__file__).resolve().parent` 自定位。
- 工作区根目录约定为环境变量 `AGENT_WORKSPACE`（默认 `~/agent-workspace`），凭据文件默认 `$AGENT_WORKSPACE/.env`，脚本优先读进程环境变量，其次读 `--env-file` 或上述默认文件。

## 2. manifest.json

```jsonc
{
  "schema_version": "agentjarvis.catalog/v1",
  "name": "gmail-read",                  // 全局唯一 id，小写连字符；= 目录名
  "type": "skill",                       // skill | workflow
  "version": "1.0.0",                    // semver
  "title_zh": "Gmail 收信读取", "title_en": "Gmail Reader",
  "summary_zh": "……一两句……", "summary_en": "……one or two sentences……",
  "tags": ["email", "跨境电商"],
  "category": "communication",           // 见 §4 分类表
  "audience": ["ecommerce", "creator", "developer"],
  "value": "high",                       // high | medium | low（对外部用户的通用价值，官方自评）
  "entry": "SKILL.md",                   // skill: SKILL.md；workflow: scaffold/CLAUDE.md
  "requires": {
    "harness": ["claude_code", "codex", "opencode"],
    "os": ["linux", "macos", "windows-wsl"],
    "cli": [{"name": "python3", "version": ">=3.10"}, {"name": "ffmpeg", "optional": true}],
    "python_requirements": "requirements.txt",   // 没有则 null
    "env": [{"name": "GMAIL_ADDRESS", "required": true, "secret": false, "description_zh": "……", "how_to_get": "……"}],
    "accounts": [{"name": "Google 账号（开启两步验证 + 应用专用密码）", "url": "https://myaccount.google.com/apppasswords"}],
    "skills": []                         // 依赖的其它广场包 name（workflow 常用）
  },
  "install": {
    "skill_dir_name": "gmail-read",      // 装到 <harness skills 目录>/<skill_dir_name>/
    "post_install": ["python3 -m pip install -r requirements.txt  # 建议在 venv 里"],
    "verify": "python3 scripts/gmail_read.py --help"   // 安装器跑它判断装好没（不得需要凭据、不得出网）
  },
  "files": [{"path": "SKILL.md", "sha256": "…", "bytes": 1234}],   // 由 tools/build_manifest.py 生成，勿手写
  "license": "Proprietary (Agent Jarvis 官方，付费用户授权使用)",   // 上游开源的写上游许可，如 "MIT"
  "upstream": null,                      // 改编自开源上游时：{"name","license","url","modified":true}
  "author": "Agent Jarvis 官方",
  "certified": true,
  "verification": {"level": "live-key", "date": "2026-10-02", "note_zh": "IMAP 读最近 1 封"},   // 可选；缺省 = 离线验证
  "updated_at": "2026-10-02"
}
```

字段规则：
- `files` 覆盖包内**全部**文件（manifest.json 自身除外），平台导入时逐个校验 sha256；多出或缺少文件即拒收。
- `requires.env[].secret=true` 的变量，安装器只能引导用户填进本机凭据库，绝不经平台中转。
- `install.verify` 必须离线、无凭据可跑，返回 0 即安装成功。
- `verification`（可选）：`level` 取 `offline`（只跑过离线 verify / 自测）或 `live-key`（用真凭据对真实服务跑通过最小一次调用），`date` 为实测日期，`note_zh` 写测了什么、没测什么。缺省视为 `offline`。CATALOG 的「验证」列由它生成。调付费或外部 API 的包，代码改动后应重新实测再更新日期。
- workflow 包额外字段：`"roles": [{"name","file","summary_zh"}]`、`"automations": [{"id","schedule","approval","default":"dormant"}]`、`"params_schema": "params.schema.json"`。automation 一律默认休眠，客户第一次手动跑通后再启用。
- `automations[].approval` 枚举：`none`（只动本地、可逆）· `read-only`（只读巡检）· `review`（产出等人看，不对外）· `publish`（对外发布 / 部署 / 合并 / 花钱，每次都要人在手机上批准）。`schedule` 可以是 cron 式字符串，或用 `schedule_param` 指向某个参数名，由客户填。
- `params.schema.json` 是 JSON Schema draft-07，另有两个扩展：`x-auto`（`today` / `now` / `today+Nd` / `harness`，由安装器在安装当刻计算、不问用户，`default` 只是兜底的静态值）；`readOnly: true` 表示不在向导里展示。
- **harness 统一写法**：manifest `requires.harness` 与 params 里的 `harness` 参数一律用 `claude_code` / `codex` / `opencode`（下划线，不用 `claude-code`）。由安装器自动填的 harness 参数写 `"x-auto": "harness"`，不再用 `installer-harness`。
- **日期一律安装时生成**：scaffold 里出现的安装日期、`stale_after` 等日期参数必须带 `x-auto`（`today` / `today+Nd`）且 `readOnly: true`；`default` 里的静态日期只是兜底，安装器不得直接使用。
- **只读巡检用 `read-only`**：只读工作区、唯一的写入是 `reports/` 下自己的报告、不调用付费 API、不对外的定时任务标 `read-only`，不标 `none`（agent 运行本身消耗的模型额度不算付费 API）。会改工作区内容（起草、编译、写稿、写状态库）的至少 `review`；调用付费 API（TTS、生图、云构建等）或对外的标 `publish`。
- `install.verify` 的路径相对于**包根目录**（workflow 包里通常是 `scaffold/tools/...`）。安装器应在解包后、渲染前跑它；渲染后的工作区里，同一个工具路径会去掉 `scaffold/` 前缀。
- 安装到已有目录时，`scaffold/` 里的 `CLAUDE.md` / `AGENTS.md` / `.gitignore` 可能和用户已有文件冲突：安装器先备份，再走三方合并（与 `15` §4「基线 / 参数 / 覆盖层」同一机制），不得静默覆盖。
- Bible 要求五个生命周期 skill 放在项目本地。首批包只在 `scaffold/.claude/skills/` 放了 `start-session` 和 `finalize-session`，另外三个（`self-reflection`、`self-reflection-cli`、`slim-docs`）写在 `requires.skills` 里，由广场装成全局版。安装器可以选择把这三个也渲染进项目本地。

## 3. 安装目标（安装器侧）

| harness | skill 安装目录 | workflow 落地 |
|---|---|---|
| Claude Code | `~/.claude/skills/<skill_dir_name>/` | `$AGENT_WORKSPACE/<name>/`（scaffold 渲染后） |
| Codex | `~/.agents/skills/<skill_dir_name>/`（`.claude`→`.agents`、`CLAUDE.md`→`AGENTS.md` 由安装器转换） | 同上；`scaffold/.claude/agents/*.md` 另生成 `.codex/agents/*.toml` |
| OpenCode | `~/.config/opencode/skills/<skill_dir_name>/` | 同上 |

## 4. 分类（category）

`agent-ops`（会话与工作流运维）· `security` · `browser` · `communication` · `content`（文案/翻译）· `media`（TTS/图/视频）· `seo` · `storage` · `data` · `workflow`（工作流包专用）

## 5. 示例虚构实体（全库统一，别自己再发明）

| 用途 | 中文 | English |
|---|---|---|
| 跨境电商卖家品牌 | 青禾家居 | Greenleaf Home |
| 内容频道 | 晨读研究所 | Morning Lab |
| 出版社 | 拾光出版 | Lumen Press |
| 软件产品 | 星图 App | StarChart |
| 知识库 | 青禾百科 | Greenleaf Wiki |
| 人名 | 张三 / 李四 | Alex / Sam |
| 域名 / 邮箱 | `example.com` · `shop.example.com` | `you@example.com` |

## 6. 发布闸门

每包必须：`python3 tools/check_pkg.py <包目录>` 全绿（manifest 校验 + 文件哈希 + 脱敏 denylist + 绝对路径 + 凭据形状）→ `secret-gate`（public 三道）→ 贾维斯人工逐包复核。

## 7. 平台侧必须实现（包里不绕过）

下面这些是安装器 / 广场后端的职责。包作者不应在包里自己绕过（比如把参数预先转义），否则平台修好后会双重处理。

1. **按目标文件类型转义渲染**：渲染 `{{param}}` 时，`.json` 文件里的值必须做 JSON 字符串转义（`"`、`\`、控制字符），`.toml` / `.yaml` 同理按各自语法转义；`.md` 等纯文本原样替换。渲染后对每个 `.json` 做一次 `json.loads`，失败即中止安装并回滚。占位符匹配用 `\{\{([a-z][a-z0-9_]*)\}\}`（参数名可含数字，如 `stale_after_14d`、`e2e_cmd`）；渲染后仍残留 `{{…}}` 即中止。
2. **签字确认步骤**：scaffold 文档头的 `verified: [{ by: human:{{chairman}}, at: {{install_date}} }]` 代表董事长签字。安装向导最后一步必须单独展示「以下文档将以你的名义签字生效」并列出文件，用户点确认后才能渲染；用户不确认则渲染成 `verified: []`，交给首次 `/start-session` 逐份签字。
3. **x-auto 计算**：`today` / `now` / `today+Nd` / `harness` 由安装器在安装当刻计算；`readOnly: true` 的参数不在向导里展示。
4. **凭据文件只追加不覆盖**：引导用户填 `.env.example` 里的变量时，目标是共享的 `$AGENT_WORKSPACE/.env`。安装器只能追加缺失的变量名（已存在的键保留原值），绝不整份覆盖；写入前备份，文件权限 600。
5. **automation 的 approval 档位由平台强制**：`publish` 每次执行前都要人在手机上批准；`review` 的产物必须进待审队列、不得自动对外；`read-only` 运行时除 `reports/` 外工作区只读挂载，或至少在执行后校验 `reports/` 之外无写入。automation 一律默认休眠，客户首次手动跑通后再启用。
6. **批准凭证由平台落盘**：工作流里的 `approve --by <称呼>`（如 content-channel-pipeline 的 `approval.json`）只能核对传入的称呼，证明不了是董事长本人操作。平台在手机上完成 `publish` 批准后，应把批准人、时间和批准时的成品哈希写入（或签名）该任务的批准文件；agent 自己运行 `approve` 只能作为没有平台时的兜底。
7. **`install.verify` 运行环境**：解包后、渲染前，在断网、无凭据、临时 HOME 的环境里跑；返回 0 才算安装成功。

