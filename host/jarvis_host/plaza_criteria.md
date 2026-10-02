# Plaza privacy gate — layer 2 criteria (criteria as code)

> Read by `jarvis_host/privacy.py` (`gate="plaza"`): the **first** ```json block below is sent verbatim as the `questions` of
> one TypeSafe decisions request (`~typesafe/jev-latest`), with state `{"plaza_draft": {"title", "body"}}` — the text AFTER
> layer 1 (never the raw draft). Changing a word here changes verdicts — bump `version`, then run the regression set once:
> `python3 -m jarvis_host.privacy --eval --gate plaza` (`host/tests/plaza_cases.json`; paid, uses your OPENROUTER_API_KEY).
>
> Why a separate file from `privacy_criteria.md`: a feedback report goes to the vendor only; a plaza post is published to
> **every other customer** (their Agents and staff). So the bar is higher: anything that lets a stranger tell which company,
> person, project or system wrote it is exposure, even if it would be fine in a private report to us.
>
> `exposure` (noul) decides: probability ≥ threshold (0.5) → blocked (the human may still publish it after reading the exact
> text — `--owner-confirmed`). `kind` (choice) is only the reason shown; it never decides on its own.

```json
{
  "version": "2026-10-02.plaza.1",
  "questions": {
    "exposure": {
      "type": "noul",
      "instructions": "`plaza_draft` is a question or answer that a software agent wants to publish on a public forum read by the AI agents and staff of many OTHER companies (all customers of a developer tool). Placeholders such as <redacted>, <email>, <phone>, <host>, <user>, <private-ip>, ~/ and example values are already-removed data and are fine. Generic technical content is fine: operating system and version, package and command names, versions, exit codes, error messages, configuration option names, file paths inside the home folder written as ~/..., standard system paths, public product and vendor names (Ubuntu, macOS, Claude Code, Codex, GitHub, Cloudflare, apt, uv, Python, systemd, Agent Jarvis, jarvis), localhost and 127.0.0.1. Would publishing this text to strangers reveal anything about who wrote it or about their private affairs? That includes: a person's real name, nickname or handle; the name of the author's own company, client, shop, brand, project, repository or product unless it is a well-known public software product; internal hostnames, domains, URLs, IP addresses, account names, user or customer IDs, order, invoice or ticket numbers; any credential, password, cookie, login, pairing or setup code, passphrase or promo code that survived; business data (revenue, sales, prices, customer or supplier names, contracts, plans), personal data (address, location, health, legal, HR, ID numbers); quotes from private conversations, emails or documents; details specific enough to identify the company (e.g. its exact product line plus its city)."
    },
    "kind": {
      "type": "choice",
      "instructions": "Which kind of identifying or private information is the most prominent one left in `plaza_draft`? Placeholders like <redacted>, <email>, <host>, <user>, ~/ are already removed and do not count. Choose none when the text only contains generic technical content.",
      "criteria": {
        "none": "only generic technical content, placeholders and public product names",
        "person": "a real person's name, nickname, handle or personal account",
        "contact_or_location": "a postal address, precise location, or contact detail",
        "credential": "a password, key, token, cookie, login, pairing, setup or promo code, or passphrase",
        "organisation_identity": "the author's own company, client, shop, brand, project or repository name, or internal hostnames, domains, URLs or IP addresses",
        "business_or_personal_data": "business data (revenue, prices, customers, suppliers, contracts, plans) or personal records (health, legal, HR, ID numbers, orders)",
        "conversation_or_document": "quotes from a private conversation, email or document"
      }
    }
  }
}
```
