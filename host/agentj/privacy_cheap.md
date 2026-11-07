# #391 cheap review / 廉价审核

The default remains the existing Jev route. `jev.cheap` is an inactive candidate;
writing it, or editing `jev.mode`, does not activate weaker review. Do not add a
bundled skill, read a key into chat, or select a model/price from memory.

Owner configuration uses an existing `core.presets` provider/model, `max_output`
(1–800), `threshold` (0 < threshold <= 1), and `evaluation`. Evaluation metadata
contains exactly `source`, `model`, `threshold`, `source_file`, `source_sha256`,
`jev`, `cheap`. Each route's counts are `tp`, `fn`, `fp`, `tn`, `unavailable`.
Only declared `source: real`, complete positive and negative classes, identical
class counts and cheap recall >= Jev recall are reviewable. Unknown/synthetic
receipts cannot qualify. Use the SAME reviewed source cases, redaction and
threshold with `privacy.run_eval`; retain the actual source file SHA-256 and
both model-run records. Counts are review metadata, not proof that a paid run
occurred. The owner must check its provenance; never relabel synthetic runs real.

`privacy_cheap.review` returns the digest declaration covering the full candidate
and eval metadata. Settings → Safety shows the candidate review button. It displays
the candidate metadata and uses existing `approval_set` for `jev.mode`; the host
requires this phone's own UV, existing signature/nonce checks and a live paired
signing key. Every resolution compares the current settings digest to the signed
choice. Editing the model, threshold, token limit or receipt invalidates it.
There is no new proof store or automatic promotion.

The resident support-bot runtime uses `runtime_layer2` through the existing core
provider request channel and existing bot token reservation/settlement ledger.
Only configured, non-plan OpenAI-chat presets are currently supported; endpoint
and model come from the shipped preset schema, never candidate URLs. Requests have
strict JSON schema, a bounded output and input size, no tools, a 20-second timeout,
no retry and no fallback. Malformed, truncated, tool-bearing or failed responses
are unavailable; uncertain usage remains fully reserved. Other Jev consumers that
lack this resident budget context fail closed under a signed cheap choice; they
do not silently switch models or skip review. Keep Jev for those uses.

Offline `recorded_layer2` and `compare` remain synthetic engineering tools. Their
20-case suite covers injection, abuse, group, friend and support; even perfect
scores leave activation NOT_VERIFIED. Tests inject synthetic transport into the
production request function; they establish request/budget/parser behavior, not
model recall, cost, speed or production readiness. Actual paid Jev/cheap comparison
and owner activation are external dependencies and were NOT performed here.

中文：默认保持 Jev；候选配置不等于启用。真实评测须同一来源文件、脱敏、阈值和正负样本，
保留来源文件 SHA-256、两路模型运行记录及计数。synthetic/未知 receipt 一律拒绝，
不能把工程测试重标成真实资格。现有手机审核展示来源、模型、阈值、文件哈希、计数，
本人 UV 加 approval_set 签名绑定整个候选；手改配置或失效设备不能弱化审核。
当前运行时闭合限客服现有预算上下文、既有非套餐 OpenAI-chat provider；其他上下文
缺预算时 failclosed，不静默 fallback。真实付费对比与主人启用均未执行，activation
保持 NOT_VERIFIED，不宣称真实性能或权益已同步。
