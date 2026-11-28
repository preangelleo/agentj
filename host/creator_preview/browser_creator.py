"""Fixed read adapters for the signed-in creator account in the Ego profile."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import re
import secrets
import time
from urllib.parse import urlsplit

from agentj.browser_cdp import CDPError


@dataclass(frozen=True)
class Site:
    origin: str
    account: re.Pattern
    actions: tuple[str, ...]
    endpoints: tuple[str, ...]


SITES = {
    "bilibili": Site("https://member.bilibili.com", re.compile(r"[1-9][0-9]{0,15}"),
                     ("archive_status",), (
                         "https://api.bilibili.com/x/web-interface/nav",
                         "https://member.bilibili.com/x/web/archives?status=is_pubing%2Cpubed%2Cnot_pubed&pn=1&ps=10&coop=1",
                     )),
    "youtube-studio": Site("https://studio.youtube.com", re.compile(r"UC[A-Za-z0-9_-]{22}"),
                          ("channel_status",), ("https://studio.youtube.com/channel/",)),
}


# This code runs in an isolated world attached to a host-created, inert top
# document. It never evaluates provider code or returns raw page or response data.
_READ = r"""
async (cfg) => {
  const { marker, account, endpoints, site, operation, deadline } = cfg;
  if (top !== self || location.href !== marker) throw Error("Page changed");
  async function read(url, contentType) {
    const left = deadline - Date.now();
    if (left <= 0) throw Error("Grant expired");
    const res = await fetch(url, {
      method: "GET", credentials: "include", redirect: "error", referrerPolicy: "no-referrer",
      cache: "no-store", mode: new URL(url).origin === location.origin ? "same-origin" : "cors",
      signal: AbortSignal.timeout(Math.min(4000, left)),
    });
    if (!res.ok || res.url !== url || !res.headers.get("content-type")?.toLowerCase().startsWith(contentType)) {
      throw Error("Provider response unavailable");
    }
    const reader = res.body.getReader(), decoder = new TextDecoder();
    let data = "", size = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) return data + decoder.decode();
        size += value.byteLength;
        if (size > 2 * 1024 * 1024) throw Error("Provider response too large");
        data += decoder.decode(value, {stream: true});
      }
    } finally {
      void reader.cancel().catch(() => {});
    }
  }

  if (site === "bilibili") {
    async function itsAccount() {
      const nav = JSON.parse(await read(endpoints[0], "application/json"));
      return nav.code === 0 && nav.data?.isLogin === true &&
        Number.isSafeInteger(nav.data.mid) && String(nav.data.mid) === account;
    }
    if (!await itsAccount()) return null;
    if (operation === "verify") return {account};
    const records = JSON.parse(await read(endpoints[1], "application/json"));
    const rows = records.data?.arc_audits;
    if (records.code !== 0 || !Array.isArray(rows) || rows.length > 10) return null;
    const archives = [];
    for (const row of rows) {
      const arc = row?.Archive;
      if (!arc || !Number.isSafeInteger(arc.mid) || String(arc.mid) !== account ||
          typeof arc.bvid !== "string" || !/^BV[A-Za-z0-9]{10}$/.test(arc.bvid) ||
          !Number.isSafeInteger(arc.state) || arc.state < -100 || arc.state > 100) return null;
      archives.push({id: arc.bvid, state: arc.state});
    }
    return await itsAccount() ? {account, archives} : null;
  }

  if (site === "youtube-studio") {
    // The targeted dashboard and its signed-in channel declaration must agree.
    // The HTML is examined as text, with no parser that can load remote assets.
    const source = await read(endpoints[0] + account, "text/html");
    const assignments = [...source.matchAll(/<script(?: [^<>]*)?>\s*ytcfg\.set\((\{[^<]*\})\);?\s*<\/script>/g)];
    if (assignments.length !== 1) return null;
    const config = JSON.parse(assignments[0][1]);
    if (config.CHANNEL_ID !== account || config.LOGGED_IN !== true) return null;
    return {account};
  }
  return null;
}
"""


# Fulfill the initial navigation locally, then allow the empty document to
# make just the adapter's closed GETs. Never fall back to direct navigation.
_BOOTSTRAP = r"""
  const cdp = (method, params = {}) => page.cdp(method, params, {timeout: 15000});
  const root = async () => (await cdp("Page.getFrameTree")).frameTree;
  const initial = await root();
  if (initial.frame.url !== "about:blank" || initial.childFrames?.length) throw Error("Page unavailable");
  await cdp("Network.setCacheDisabled", {cacheDisabled: true});
  await cdp("Network.setBypassServiceWorker", {bypass: true});
  await page.events();
  await cdp("Fetch.enable", {patterns: [{urlPattern: "*", requestStage: "Request"}]});
  let navError;
  const nav = cdp("Page.navigate", {url: cfg.marker}).catch(e => { navError = e; });
  let fulfilled = false;
  const end = Date.now() + 3500;
  for (;;) {
    const events = await page.events();
    if (!Array.isArray(events)) throw Error("Request interception unavailable");
    for (const event of events) {
      if (event.method !== "Fetch.requestPaused") continue;
      const p = event.params;
      if (!p?.requestId) throw Error("Request interception unavailable");
      if (fulfilled || p.frameId !== initial.frame.id || p.request?.method !== "GET" ||
          p.request.url !== cfg.marker || p.resourceType !== "Document") {
        await cdp("Fetch.failRequest", {requestId: p.requestId, errorReason: "BlockedByClient"});
        throw Error("Unexpected request");
      }
      await cdp("Fetch.fulfillRequest", {
        requestId: p.requestId, responseCode: 200, responseHeaders: cfg.headers, body: cfg.body,
      });
      fulfilled = true;
    }
    if (fulfilled) break;
    if (navError || Date.now() >= end) throw Error("Request interception unavailable");
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  const loaded = await nav;
  if (navError || !loaded?.loaderId || loaded.errorText || loaded.isDownload) throw Error("Page unavailable");
  const same = async () => {
    const current = await root();
    return !current.childFrames?.length && current.frame.id === initial.frame.id &&
      current.frame.loaderId === loaded.loaderId && current.frame.url === cfg.marker &&
      current.frame.securityOrigin === cfg.origin;
  };
  if (!await same()) throw Error("Page unavailable");
  await cdp("Fetch.disable");
  const world = await cdp("Page.createIsolatedWorld", {frameId: initial.frame.id, worldName: "AgentJCreatorRead"});
  if (!Number.isInteger(world.executionContextId)) throw Error("Page unavailable");
  const result = await cdp("Runtime.evaluate", {
    expression, contextId: world.executionContextId, returnByValue: true, awaitPromise: true,
  });
  if (result.exceptionDetails || !await same()) return null;
  return result.result?.value ?? null;
"""


def script(site: str, account: str, operation: str, until=None) -> str:
    from agentj.browser_ego import _json
    rule = SITES[site]
    if not rule.account.fullmatch(account) or operation not in ("verify", *rule.actions):
        raise CDPError("Creator request unavailable")
    marker = f"{rule.origin}/.well-known/agentj-creator-{secrets.token_hex(16)}"
    connect = " ".join(dict.fromkeys(f"https://{urlsplit(endpoint).hostname}" for endpoint in rule.endpoints))
    policy = f"default-src 'none'; img-src data:; connect-src {connect}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    config = {
        "site": site, "origin": rule.origin, "marker": marker, "operation": operation, "account": account,
        "endpoints": rule.endpoints,
        "headers": [{"name": "Content-Type", "value": "text/html; charset=UTF-8"},
                    {"name": "Content-Security-Policy", "value": policy},
                    {"name": "Cache-Control", "value": "no-store"},
                    {"name": "Referrer-Policy", "value": "no-referrer"}],
        "body": base64.b64encode(
            b'<!doctype html><title>Agent J Creator Read</title><link rel="icon" href="data:,">'
        ).decode("ascii"),
    }
    read_config = {key: config[key] for key in ("site", "marker", "account", "endpoints", "operation")}
    ceiling = time.time() + 20
    read_config["deadline"] = int(min(until, ceiling) * 1000 if until is not None else ceiling * 1000)
    expression = f"({_READ})({_json(read_config)})"
    return f"const cfg = {_json(config)};\nconst expression = {_json(expression)};\n" + _BOOTSTRAP


def project(site: str, account: str, operation: str, value) -> dict | None:
    """A second, strict schema check before any result leaves the host."""
    if not isinstance(value, dict) or value.get("account") != account:
        return None
    if operation != "archive_status":
        return {"account": account} if set(value) == {"account"} else None
    rows = value.get("archives")
    if set(value) != {"account", "archives"} or not isinstance(rows, list) or len(rows) > 10:
        return None
    if any(not isinstance(row, dict) or set(row) != {"id", "state"} or not isinstance(row["id"], str)
           or not re.fullmatch(r"BV[A-Za-z0-9]{10}", row["id"]) or type(row["state"]) is not int
           or not -100 <= row["state"] <= 100 for row in rows):
        return None
    return {"account": account, "archives": rows}


def read(rt: dict, site: str, account: str, operation: str, live, until=None) -> dict | None:
    """Make one fixed creator read in a new Agent-owned Page, never a user tab."""
    from agentj.browser_ego import EgoCDP
    body = script(site, account, operation, until)
    cdp = EgoCDP(rt)
    target = cdp.new_tab("about:blank")
    try:
        if not live():
            return None
        return project(site, account, operation, cdp._page(target, body, timeout=20))
    finally:
        cdp.close_tab(target)
