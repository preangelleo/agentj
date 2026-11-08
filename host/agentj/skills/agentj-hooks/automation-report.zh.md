# 自动化报告

## 场景：自动化报告交给主 Agent（Webhook / HTTP API）

跟 Agent 说「把自动化的成功和失败都交给你」。用中性的 `automation-report` 预置，默认常驻、Bearer 验证、送到 `main`，不按成功失败过滤。创建常驻入口仍须主人在已配对设备上批准：

```bash
agentj hook create --preset automation-report --name automation-reports
```

每次完成都投一份报告，`status` 建议用 `success`、`failure` 或 `partial`，也可用工作流自己的状态。它是报告数据，不是执行指令，不能授予权限或让 Agent 运行日志里的命令。

```json
{"status":"success","automation":"nightly-backup","unit":"nightly-backup.service","summary":"Backup completed","report_path":"reports/backup.md","links":["https://example.com/runs/42"]}
```

默认全部进主线程：成功由主 Agent 总结，告诉你「FYI，无需处理」；失败由它在已有授权和权限内安排检查、修复，给你一句问题和实际进度；处理不了则在 Web App 说清要你做什么。不要把「已接收」当作「已修复」，也不要把未经安排的工作写成「已派人」。原始报告可在输入卡展开，人话回执是你平时看的入口。

### 本机与远端各怎么接

- **本机：** cron、systemd timer、launchd、Windows 计划任务在任务结束后运行投递脚本；用退出状态生成 `status`，成功失败都投。可用托管 HTTP API，也可用 `agentj hook forward` 留在本机，仍走该 hook 原有验证与权限边界。
- **远端：** GitHub Actions、云函数在完成/异常处理分支调用托管 HTTP API；本机 `forward` 不能从远端直接访问。自建云入口先检查来源签名，再经你自己的安全连接转给主机上的 `forward`；不要把本机 socket 暴露上网。

下面给出同一脚本的两种接法。为了不把 Bearer 密钥放进命令参数，例子覆盖预置的验证方式，使用带时间戳的 HMAC：

```bash
agentj hook create --preset automation-report --auth hmac --signed ts.body --name automation-reports
```

保存下面代码为 `report.py`。把创建结果中的 `secret_file` 配到本机任务的 `AUTOMATION_SECRET_FILE`；本机转入另配 `AUTOMATION_HOOK_ID`，托管投递另配 `AUTOMATION_REPORT_URL`。远端把密钥放在 CI/云平台的秘密库，用 `AUTOMATION_REPORT_SECRET` 注入。这里只引用名字，不把值写进仓库、日志或对话。

```python
# report.py: Python 3, standard library only. Never print credentials or headers.
import hashlib, hmac, json, os, subprocess, sys, time, urllib.request
from pathlib import Path

with Path(sys.argv[1]).open("rb") as report:
    body = report.read(65537)
if len(body) > 65536:
    raise SystemExit("Report exceeds 64 KiB; send a redacted summary and links")
json.loads(body)  # Reject malformed reports before sending.
secret = (Path(os.environ["AUTOMATION_SECRET_FILE"]).expanduser().read_bytes().strip()
          if os.environ.get("AUTOMATION_SECRET_FILE")
          else os.environ["AUTOMATION_REPORT_SECRET"].encode())
ts = str(int(time.time()))
sig = hmac.new(secret, ts.encode() + b"." + body, hashlib.sha256).hexdigest()
headers = {"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig}
if os.environ.get("AUTOMATION_HOOK_ID"):  # Local forward; same signature check.
    args = ["agentj", "hook", "forward", os.environ["AUTOMATION_HOOK_ID"], "--body", "-"]
    for key, value in headers.items():
        args += ["--header", key + ": " + value]
    subprocess.run(args, input=body, check=True)
else:  # Hosted HTTP API, including a cloud function or CI runner.
    req = urllib.request.Request(os.environ["AUTOMATION_REPORT_URL"], data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        if response.status != 200:
            raise SystemExit("Delivery was not accepted")
```

```bash
python3 report.py report.json
agentj hook logs automation-reports
```

Linux 的 cron/systemd、macOS 的 launchd、Windows 计划任务都调用上面一行，配置绝对脚本路径和任务自己的工作目录。先用脱敏的成功、失败、部分失败报告各跑一次，核 `hook logs` 和 Web App 的输入卡、回执，再启用日程。

GitHub Actions 示例（将已保存的投递脚本放在 `scripts/report.py`；云函数也可执行同一 Python 逻辑）：

```yaml
- name: Report result
  if: always()
  env:
    AUTOMATION_REPORT_URL: ${{ secrets.AUTOMATION_REPORT_URL }}
    AUTOMATION_REPORT_SECRET: ${{ secrets.AUTOMATION_REPORT_SECRET }}
    JOB_STATUS: ${{ job.status }}
  run: |
    python3 -c 'import json,os; json.dump({"status":os.environ["JOB_STATUS"],"automation":"ci","summary":"CI finished"},open("report.json","w"))'
    python3 scripts/report.py report.json
```

托管请求默认明文经过中继内存；需要保密的正文用本页的端到端加密接法，或自建入口。本机/远端只投必要的脱敏片段和链接，不投密钥、整份环境或无界日志；本机路径在远端和手机上不一定可访问。主机离线会返回 503，发送方须按自己的重试策略补投；成功收据仅表示接收，不保证 Agent 已完成处理。

**可选变体：只投失败，成功走 Telegram。** 在发送方过滤 `status`，成功用你已有并已授权的 Telegram 通道，失败/部分失败投主 Agent。Telegram 不是前提，也不是预置默认；不用 Telegram 时，保持全部投主线程即可。

