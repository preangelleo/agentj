---
name: agentj-pair
description: Guide the owner through pairing an Agent J remote (phone or browser). Mandatory after account binding during installation. Triggers include 添加遥控器、配对手机、绑定成功、add a remote, pair my phone.
---

# Pair a remote on the owner's computer

1. Read the matching language's pairing section in `https://agentj.app/install.md` downloaded as a local file. During installation, account binding is an intermediate step, not completion.
2. Actually run `agentj devices --json`. If no remote is paired, proactively guide the owner through pairing before reporting installation complete.
3. Run `agentj admin` on the computer running this Agent. The Dashboard's **添加遥控器 / Add a remote** opens `agentj://pair` on that same computer; if the protocol is not registered, run `agentj protocol install`. On a headless server, use the install guide's SSH loopback instructions or `agentj pair`.
4. The QR code, one-use link, six-digit code entry and approval stay on this computer. Only the owner approves. Never put pairing material or a local admin link in cloud metadata, support messages, logs, reports or git. The Dashboard cannot approve or add a device.
5. Verify a real remote is listed and send the first message from it. Only then finish installation. Do not introduce pairing as an extra permission lock on ordinary tasks.
6. Moving a seat to a different computer needs fresh pairing. History stays on the old computer.

账号管付钱，席位管功能。一个邮箱一个账号；一个账号可以买多个席位。手机或浏览器叫「遥控器」，Agent 是绑定席位的电脑或服务器。
