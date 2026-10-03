# Feedback privacy gate — layer 2 criteria (criteria as code)

> Read by `agentj/privacy.py`: the **first** ```json block below is sent verbatim as the `questions` of one
> TypeSafe decisions request (`~typesafe/jev-latest`). Changing a word here changes verdicts — bump `version`, then
> run the regression set once (`python3 -m agentj.privacy --eval`, see `host/tests/privacy_cases.json`).
>
> State sent with it: `{"feedback_draft": <the draft AFTER layer 1>}` — never the raw draft. Layer 1 has already
> replaced keys/tokens, emails, phone numbers, home paths, this machine's host and user name, private IPv4
> addresses and our `aj?_` ids with placeholders, so the questions below are about what regex cannot see.
>
> `exposure` (noul) decides: probability ≥ threshold (default 0.5) → blocked. `kind` (choice) is only the reason
> shown to the human; it never decides on its own.

```json
{
  "version": "2026-10-02.1",
  "questions": {
    "exposure": {
      "type": "noul",
      "instructions": "`feedback_draft` is an installation problem report that a software agent wants to send to the vendor of a developer tool. Placeholders such as <redacted>, <email>, <phone>, <host>, <user>, <private-ip>, ~/ and example values are already-removed data and are fine. Technical facts are fine: operating system and version, package and command names, versions, exit codes, error messages, file paths inside the home folder written as ~/..., standard system paths, public product and vendor names (Ubuntu, Claude Code, Codex, GitHub, Cloudflare, apt, uv, Python, systemd), localhost and 127.0.0.1, public DNS resolvers. Does the draft still contain information that identifies or exposes a real person, a customer organisation, or their private data? That includes: a person's real name or nickname; a postal or street address or precise location; the name of the customer's own company, client, project or product when it is not a public software product; internal hostnames, domains or URLs of the customer's organisation; public IP addresses of the customer's network; account names, user IDs, order numbers, invoice or ID-card numbers; any credential, password, cookie, login or pairing code, passphrase or promo code that survived; business data such as revenue, customer lists, contracts, prices agreed with clients, medical, legal or HR details; the contents of private conversations or documents."
    },
    "kind": {
      "type": "choice",
      "instructions": "Which kind of private information is the most prominent one left in `feedback_draft`? Placeholders like <redacted>, <email>, <host>, <user>, ~/ are already removed and do not count. Choose none when the draft only contains technical facts about the installation.",
      "criteria": {
        "none": "only technical installation facts, placeholders and public product names",
        "person": "a real person's name, nickname, or personal account name",
        "contact_or_location": "a postal address, precise location, or contact detail of a person or organisation",
        "credential": "a password, key, token, cookie, login, pairing or promo code, or passphrase",
        "organisation_internal": "the customer's own company, client or project name, internal hostnames, domains, URLs or public IP addresses",
        "business_or_personal_data": "business data (revenue, customers, contracts, prices) or personal records (medical, legal, HR, ID numbers, orders)",
        "conversation_or_document": "the contents of a private conversation, email or document"
      }
    }
  }
}
```
