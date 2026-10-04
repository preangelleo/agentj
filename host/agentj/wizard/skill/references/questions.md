# The interview — at most 20 questions, one per message

Ask in the human's language (Chinese wording first, English after; translate for any other language). Each question:
one short paragraph, numbered options or an example, and 「可以说跳过 / 不知道」. Accept free text, numbers ("1,3") or a
mix. Write down *what they said*; anything skipped becomes 「待定」 / "TBD".

The letters map to the Workflow Design Bible intake (A company & goals · B business structure · C team & approvals ·
D tools & existing automation · F automation level · G pace · H third-party services). Environment facts (E) you look up
yourself. → shows where the answer goes (`references/files.md`).

| # | Ask (中文 / English) | Options or example | → |
|---|---|---|---|
| 1 | 先认识一下：公司或店铺叫什么？一句话说说你们卖什么。/ What is the company or shop called, and what do you sell, in one sentence? | 例：「深圳一家做户外露营灯的，卖了 3 年」 | IDENTITY, CONSTITUTION mission |
| 2 | 你们是哪一类？/ Which describes you? | 1 中国卖家出海 · 2 海外品牌独立站 DTC · 3 代运营服务商 · 4 工厂转型出海 · 5 其他 | IDENTITY |
| 3 | 团队大概几个人？/ How many people? | 1) 1–5 · 2) 6–30 · 3) 31–100 · 4) 100+ | IDENTITY, ROLES |
| 4 | 在哪些平台、哪些站点卖？/ Which marketplaces and countries? | Amazon（US/CA/UK/DE/JP…）· Walmart · Shopify 独立站 · TikTok Shop · Temu · SHEIN · Shopee / Lazada · eBay · 其他 | MEMORY, WORKFLOW |
| 5 | 大概多少个在售 SKU、每天多少单、几个店铺？区间就行。/ Roughly how many live SKUs, orders a day, shops? | 例：「200 个 SKU，日均 150 单，2 个亚马逊店」 | MEMORY |
| 6 | 怎么发货？/ How do you ship? | 1 FBA · 2 海外仓 · 3 国内直发 · 4 平台托管 · 5 混合 | MEMORY, WORKFLOW |
| 7 | 未来 6 个月最想做到的 3 件事？/ Top 3 goals for the next 6 months? | 1 少招人 · 2 不断货 · 3 广告更赚钱 · 4 上新更快 · 5 客服回复更快 · 6 对账不出错 · 7 开新平台或站点 · 8 老板少操心 | CONSTITUTION mission, WORKFLOW priorities |
| 8 | 现在最耗人的重复活是哪些？最多 3 件，说说谁在做、每周大概几小时。/ The most time-consuming repeated work (≤ 3): who does it, hours a week? | 例：「运营小李每天早上拉各平台数据做日报，约 5 小时/周」 | WORKFLOW, NEXT_SESSION |
| 9 | 哪天你会觉得「这个钱花得值」？一句话。/ What would make this worth the money, in one sentence? | 例：「每天 8 点手机上就有一份看得懂的日报」 | CONSTITUTION North Star |
| 10 | 有哪些部门或角色，各由谁负责？用职位代替真名也行。/ Which teams or roles, and who leads each? Job titles are fine instead of names. | 例：「运营 2 人（负责人：运营主管）、客服 1 人、广告我自己管」 | ROLES |
| 11 | 除了「花钱、对外发消息、改价、删除或下架」这几件**一定**先在手机上问你之外，还有哪些也要先问你？花钱超过多少要问？/ Besides spending money, messaging anyone outside, changing prices and deleting / delisting (always asked), what else must I ask you first — and above what amount? | 1 调广告预算 · 2 下采购单 · 3 上架新商品 · 4 回复差评 · 5 改 listing 文案 · 6 其他；金额例：「超过 200 元都要问」 | CONSTITUTION approvals |
| 12 | 谁能批准？/ Who may approve? | 1 只有老板 · 2 各部门负责人批本部门的 · 3 按动作分（说说怎么分） | CONSTITUTION, ROLES |
| 13 | 你在哪个时区、几点上下班？每天几点要收到报告？/ Time zone, working hours, and when should the daily report arrive? | 默认：北京时间 9:00–18:00，报告 08:00 | WORKFLOW, MEMORY |
| 14 | 数据和表格用什么工具？/ Which ERP, data and spreadsheet tools? | 领星 · 积加 · 马帮 · 店小秘 · 卖家精灵 · Helium 10 · Jungle Scout · 飞书 · Google Sheets · Excel · 没有 | ROLES tools, MEMORY |
| 15 | 客服用什么？有没有已经在跑的自动化（RPA、Zapier、脚本）？有的话最重要的 1–3 条叫什么、多久跑一次。/ Customer-service tool? Any automation already running (RPA, Zapier, scripts) — the 1–3 most important, how often? | 客服：Gorgias · eDesk · Zendesk · 平台后台 · 没有；自动化：影刀 · 按键精灵 · Zapier · Make · n8n · 扣子 · 自己写的脚本 · 没有 | ROLES, WORKFLOW (existing automation, keep running) |
| 16 | 下面这几件事，各想自动化到什么程度？「报告」= 工作流 CEO 出报告，我读回；「起草」= 工作流 CEO 起草、你在手机上批；「不要」= 先不做。/ For each, how far should I go? report = the workflow CEO reports · draft = the workflow CEO drafts, you approve on the phone · none = not now | 经营日报 · 广告复盘 · Listing 撰写与巡检 · 客服回复草稿 · 选品调研 — 例：「日报 报告，广告 报告，listing 起草，客服 起草，选品 不要」 | WORKFLOW, STRUCTURE.json workflows |
| 17 | 除了手机上的推送，还要把报告或提醒发到哪里？做文案、图片用哪家服务？**只说用哪家，不要发任何密码或 key。** / Besides phone push, where else should reports go? Which service for copy / images? Only the name — never a key. | 通知：企业微信 · 飞书 · 钉钉 · Slack · Telegram · 不需要；模型 / 图片：已有的 ChatGPT / Claude 订阅 · OpenRouter · Replicate · 暂不需要 | ROLES tools, NEXT_SESSION (to set up) |
| 18 | 要给我起个名字吗？可以用你在后台给这个 Agent 起的名字。/ Want to give me a name? You can reuse the Agent name from the Dashboard. | 建议：助理一号 · Wren · 董事长助理，或自己起；跳过 = 用「待定」 | IDENTITY, entry file |
| 19 | 希望多快用上第一个工作流？/ How soon should the first workflow be in use? | 1 这周 · 2 这个月 · 3 先试试看 | NEXT_SESSION goals |
| 20 | （确认）这是我记下的，对吗？要改哪一条？/ (Confirm) Here is what I noted — right? What should change? | 12 行以内的摘要，含「待定」清单 | — |

Notes
- Question 11: the four always-ask actions are fixed rules of this product; the human can only add to them, never remove.
- Question 16 lists the five starter templates. If a template is installed under `<id>/` (or its recorded legacy path), mention it is already
  there (dormant). Ids: 经营日报 `daily-report` · 广告复盘 `ads-review` · Listing `listing` · 客服 `customer-service` ·
  选品 `product-selection`.
- Question 17 is the only place services come up. If they ask how to set a key, say it is done later by them directly in
  that service's own settings or on this computer — never in this chat.
- Not an e-commerce business? Keep the same order and adapt the wording (platforms → channels, SKUs → products or
  services, question 16 → their own 3–5 recurring jobs).
