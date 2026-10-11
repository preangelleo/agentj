---
name: agentj-pair
description: Guide the owner through pairing an Agent J remote (phone or browser). Mandatory after account binding during installation — two required remotes, this computer's browser and the main phone. Triggers include 添加遥控器、配对手机、绑定成功、还差哪一步、add a remote, pair my phone, what's next.
---

# Pair a remote on the owner's computer

1. Read the matching language's pairing section in `https://agentj.app/install.md` downloaded as a local file. During installation, account binding is an intermediate step, not completion.
2. Actually run `agentj devices --json`. If no remote is paired, proactively guide the owner through pairing before reporting installation complete.
3. Before any pairing step, actually run the pre-check `agentj pair --check --json` (read-only; no link, QR or code). Its `route` is the same rule `agentj pair` and `agentj admin` use, so the owner never gets stuck at the last step: `account` → step 3 below (no passphrase; `agentj pair` / `agentj admin` also skip it and the waiting phone is approved on the seat card); `passphrase` → local route; `set_passphrase` → tell the owner the one sentence it prints (unbound, offline, or `remote-pair off`): they set it themselves with `agentj passphrase set` in their own terminal — or, if the host is bound, fix the reason and use the account page.
   For an account-bound online host (`account`), tell the owner: on its account-page seat card choose **添加遥控器 / Add a remote**, confirm with a passkey once, and scan with the new remote. This works from another computer. No terminal approval passphrase is required. Passkey confirmation is reused for ten minutes on this session. Pending remotes can also be reviewed and approved on that card.
4. Pairing secrets are generated on the target host and encrypted to the account browser, never visible to the Dashboard backend. Never read, log, relay or request a link/QR/passphrase. Only the owner performs the passkey ceremony. The local alternative is `agentj pair` or `agentj admin`, requiring the owner's local passphrase and six-digit code; unbound hosts still require this route. `agentj remote-pair off` disables account-page additions and owner approvals. Offline hosts are not queued; older hosts must upgrade first.
5. Verify a real remote is listed and send the first message from it. Only then finish installation. Do not introduce pairing as an extra permission lock on ordinary tasks.
6. Two remotes are required (F28): ① this computer's browser (open https://m.agentj.app here and pair it from its account seat card), then ② the owner's main phone (Home Screen icon → 「扫二维码」). Run `agentj onboarding` (or `--json`) after each step; it shows the seat, both remotes and the welcome. Pair other phones, pads or computers only when the owner asks. If the phone cannot read the QR code (F29): let the code fill most of the middle of the camera view, brighten the screen; after 10 s the phone's scanner suggests the fallback itself — the owner runs `agentj pair --link` in their own terminal and pastes the link into the Home Screen app (or, in a browser tab, scans with the phone's Camera app). The link is the pairing key: never ask for it, never relay it.
7. After the first pairing, Agent J asks the main Agent to write the first message on that remote (who it is, the first remote is ready, talk here from now on, then a short tour one step per owner reply). It happens once; later remotes get one line. Tell the owner to look at the new window instead of repeating it in the terminal.
8. Moving a seat to a different computer needs fresh pairing. History stays on the old computer.

账号管付钱，席位管功能。一个邮箱一个账号；一个账号可以买多个席位。手机或浏览器叫「遥控器」，Agent 是绑定席位的电脑或服务器。

0.16.1 defaults to the compact numeric pairing link (smaller QR); AGENTJ_PAIR_COMPACT=0 selects legacy JSON. Both are secrets and only the owner may use them.

Account passkey approval also offers resume after Stop everything and enabling a specific scheduled task. The Agent must never perform owner confirmation itself or lift the owner’s stop. The approval passphrase stays optional on bound hosts, local-only for offline/terminal approval.
