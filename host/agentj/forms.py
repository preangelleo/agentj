"""P212 (0.21, PROTOCOL §29): interactive forms in the reply card — 快选 / Quick Pick (inline) and 深聊卡 / Deep Dive
(full screen) from ONE schema, `agentj.form/v1`.

The Agent never writes HTML: it picks a template (form_templates/*.json) or writes the JSON, and `agentj ask` sends it.
- Quick Pick (light): ≤ LIGHT_MAX_Q answerable questions, one section, no attachments / ranking, ≤ 1 long text. Drawn inside
  the reply card. One single-choice / button question = a tap submits it. Anything bigger becomes a Deep Dive by itself.
- Deep Dive (deep): sections, progress, drafts kept on this computer (`form_draft`), attachments uploaded with the
  submission (the say's own blob channel, 200 MB each), one input back to the Agent.

Direction is Agent → owner only. The answer arrives as the owner's own message (a `say` with `form`), re-validated here
against the stored form (the phone is only the first check); it grants nothing (not an approval). First submission wins
(other devices see it answered); a withdrawn queued answer re-opens the form. Forms of a group member's / guest's turn are
refused. The form text is plain text (+ the md.js subset on the phone), never HTML; no password type exists — keys go
through the key card, and credential-shaped form text is refused.

Store: `<state>/forms/<id>.json` (0600; spec, state, turn, answer) and `<id>.draft.json`; the answer JSON for the Agent
lands in its inbox (`.agentj/inbox/<day>/…-form-<id>.json`). Logs carry ids, counts and reason words, never text.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import json
import pathlib
import re
import secrets
import sys
import time

FORMS_RELEASED = False  # 0342: full P212 restart awaits the owner decision.
SCHEMA = "agentj.form/v1"
ANSWER_SCHEMA = "agentj.form-answer/v1"
TYPES = ("single", "buttons", "multi", "dropdown", "text", "longtext", "number", "slider", "date", "time", "datetime",
         "rating", "rank", "file", "note")
LIGHT_TYPES = tuple(t for t in TYPES if t not in ("rank", "file"))
TAP_TYPES = ("single", "buttons")
OPT_RANGE = {"single": (2, 12), "buttons": (2, 4), "multi": (2, 12), "dropdown": (2, 60), "rank": (2, 10)}
PATTERNS = {"email": re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{1,63}"),
            "phone": re.compile(r"\+?[0-9][0-9 ()\-]{5,24}"),
            "url": re.compile(r"https?://[^\s]{3,500}")}
DATE_RE = {"date": re.compile(r"\d{4}-\d{2}-\d{2}"), "time": re.compile(r"\d{2}:\d{2}"),
           "datetime": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")}
DATE_FMT = {"date": "%Y-%m-%d", "time": "%H:%M", "datetime": "%Y-%m-%dT%H:%M"}
ACCEPT = ("image", "audio", "video", "file")
OPS = ("eq", "ne", "in", "not_in", "gt", "lt", "answered")
OTHER = "_other"
ID_RE = re.compile(r"[a-z0-9_]{1,40}")
FORM_ID = re.compile(r"f_[0-9a-f]{16}")
LIGHT_MAX_Q = 5
DEEP_MAX_SECTIONS = 12
DEEP_MAX_Q = 60
JSON_MAX = 64 * 1024
LIGHT_SPEC_MAX = 12 * 1024
FILES_MAX = 10                     # attachments of one submission (= a say's att limit)
TTL = {"light": 7 * 86400, "deep": 14 * 86400}
TTL_MIN, TTL_MAX = 3600, 30 * 86400
KEEP = 200
ROW_MAX = 160
LIM = {"title": 80, "intro": 500, "label": 200, "hint": 300, "opt": 120, "value": 80, "note": 1000, "submit": 24, "unit": 16,
       "text": 500, "longtext": 5000, "other": 200, "section": 80}
STATES = ("open", "answered", "expired", "cancelled")
TEMPLATE_DIR = pathlib.Path(__file__).with_name("form_templates")


class FormError(Exception):
    def __init__(self, why: str, detail: str = ""):
        super().__init__(f"{why}: {detail}" if detail else why)
        self.why, self.detail = why, detail


def _pick(lang: str, zh: str, en: str) -> str:
    return en if lang == "en" else zh


# ---------------------------------------------------------------- text
def loc(v, lang: str):
    """A form string: plain, or {"zh", "en"} (the form's language first, then the other)."""
    if isinstance(v, dict) and set(v) <= {"zh", "en"} and v:
        v = v.get(lang) if isinstance(v.get(lang), str) else v.get("en" if lang == "zh" else "zh")
    return v


def _clean(s: str) -> str:
    from .text import clean
    return clean(s.replace("\r\n", "\n").replace("\r", "\n"), 100_000).strip()


def _txt(v, lang: str, limit: int, where: str, required: bool = False) -> str:
    v = loc(v, lang)
    if v is None or v == "":
        if required:
            raise FormError("missing", f"{where} 必填 / required")
        return ""
    if not isinstance(v, str):
        raise FormError("shape", f"{where} 要是文字 / must be text")
    v = _clean(v)
    if len(v) > limit:
        raise FormError("too_long", f"{where} 超过 {limit} 字 / over {limit} characters")
    if required and not v:
        raise FormError("missing", f"{where} 必填 / required")
    return v


def _secret_free(strings: list[str], where: str = "form") -> None:
    from . import privacy
    for s in strings:
        if s and privacy.credentials(s):
            raise FormError("secret", "表单里不能放密钥，也不能用表单向主人要密钥：要密钥请用密钥卡（agentj secret） / no "
                                      "credentials in a form, and never ask for one with it: use the key card (agentj secret)")


def _num(v, where: str):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise FormError("shape", f"{where} 要是数字 / must be a number")
    return int(v) if isinstance(v, float) and v.is_integer() else v


# ---------------------------------------------------------------- show_if
def _cond(c, prior: dict, where: str, depth: int = 0) -> dict:
    if depth > 3 or not isinstance(c, dict):
        raise FormError("show_if", f"{where}: show_if 写错了 / malformed")
    if "all" in c or "any" in c:
        k = "all" if "all" in c else "any"
        xs = c[k]
        if len(c) != 1 or not isinstance(xs, list) or not 1 <= len(xs) <= 8:
            raise FormError("show_if", f"{where}: {k} 要 1–8 个条件 / {k} takes 1–8 conditions")
        return {k: [_cond(x, prior, where, depth + 1) for x in xs]}
    q = c.get("q")
    ops = [k for k in c if k != "q"]
    if not isinstance(q, str) or q not in prior:
        raise FormError("show_if", f"{where}: show_if 只能指向前面的题 / show_if may only name an earlier question")
    if len(ops) != 1 or ops[0] not in OPS:
        raise FormError("show_if", f"{where}: 一个比较（{' / '.join(OPS)}） / one comparison")
    op, v = ops[0], c[ops[0]]
    if op == "answered":
        if not isinstance(v, bool):
            raise FormError("show_if", f"{where}: answered 要 true / false")
    elif op in ("in", "not_in"):
        if not isinstance(v, list) or not 1 <= len(v) <= 60 or not all(isinstance(x, (str, int, float)) and not isinstance(x, bool) for x in v):
            raise FormError("show_if", f"{where}: {op} 要一组值 / a list of values")
    elif op in ("gt", "lt"):
        _num(v, where + " show_if")
    elif not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise FormError("show_if", f"{where}: {op} 要一个值 / one value")
    return {"q": q, op: v}


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def holds(c: dict | None, ans: dict) -> bool:
    """Is the condition true for these answers (hidden questions are already absent)? Same rules as forms.js holds()."""
    if not c:
        return True
    if "all" in c:
        return all(holds(x, ans) for x in c["all"])
    if "any" in c:
        return any(holds(x, ans) for x in c["any"])
    v = ans.get(c["q"])
    many = v if isinstance(v, list) else ([] if _empty(v) else [v])
    if "answered" in c:
        return (not _empty(v)) == c["answered"]
    if _empty(v):
        return "ne" in c or "not_in" in c
    if "eq" in c:
        return c["eq"] in many
    if "ne" in c:
        return c["ne"] not in many
    if "in" in c:
        return any(x in c["in"] for x in many)
    if "not_in" in c:
        return not any(x in c["not_in"] for x in many)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return v > c["gt"] if "gt" in c else v < c["lt"]


# ---------------------------------------------------------------- one question
def _options(raw, lang: str, typ: str, where: str) -> list[dict]:
    lo, hi = OPT_RANGE[typ]
    if not isinstance(raw, list) or not lo <= len(raw) <= hi:
        raise FormError("options", f"{where}: {typ} 要 {lo}–{hi} 个选项 / takes {lo}–{hi} options")
    out, seen = [], set()
    for i, o in enumerate(raw):
        w = f"{where} 选项 {i + 1}"
        if isinstance(o, (str, dict)) and not (isinstance(o, dict) and ("value" in o or "label" in o)):
            o = {"label": o}
        if not isinstance(o, dict):
            raise FormError("options", f"{w}: 要文字或 {{value, label}} / text or {{value, label}}")
        label = _txt(o.get("label", o.get("value")), lang, LIM["opt"], w, True)
        value = o.get("value", label if isinstance(o.get("label"), str) else None)
        if value is None:
            value = loc(o.get("label"), "en") or label
        if not isinstance(value, str) or not value.strip() or len(value) > LIM["value"] or value == OTHER:
            raise FormError("options", f"{w}: value 要 1–{LIM['value']} 字 / value 1–{LIM['value']} characters")
        if value in seen:
            raise FormError("options", f"{w}: value 重复 / duplicate value")
        seen.add(value)
        x = {"value": value, "label": label}
        h = _txt(o.get("hint"), lang, LIM["hint"], w + " hint")
        if h:
            x["hint"] = h
        out.append(x)
    return out


def _question(raw, lang: str, prior: dict, where: str) -> dict:
    if not isinstance(raw, dict):
        raise FormError("shape", f"{where}: 题目要是对象 / a question is an object")
    qid, typ = raw.get("id"), raw.get("type")
    if not isinstance(qid, str) or not ID_RE.fullmatch(qid):
        raise FormError("id", f"{where}: id 要 [a-z0-9_]{{1,40}} / id must match [a-z0-9_]{{1,40}}")
    where = f"{where}（{qid}）"
    if qid in prior:
        raise FormError("id", f"{where}: id 重复 / duplicate id")
    if typ not in TYPES:
        raise FormError("type", f"{where}: type 只能是 {' / '.join(TYPES)}（没有 password：要密钥用密钥卡） / no password type")
    q: dict = {"id": qid, "type": typ}
    if typ == "note":
        q["text"] = _txt(raw.get("text", raw.get("label")), lang, LIM["note"], where + " text", True)
    else:
        q["label"] = _txt(raw.get("label"), lang, LIM["label"], where + " label", True)
        if raw.get("required") is True:
            q["required"] = True
    h = _txt(raw.get("hint"), lang, LIM["hint"], where + " hint")
    if h:
        q["hint"] = h
    if typ in OPT_RANGE:
        q["options"] = _options(raw.get("options"), lang, typ, where)
        if raw.get("other") is True and typ in ("single", "multi"):
            q["other"] = True
    if typ == "multi":
        n = len(q["options"]) + (1 if q.get("other") else 0)
        lo, hi = raw.get("min", 1 if q.get("required") else 0), raw.get("max", n)
        if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= n or hi < 1:
            raise FormError("range", f"{where}: min / max 要 0 ≤ min ≤ max ≤ 选项数 / 0 ≤ min ≤ max ≤ options")
        q["min"], q["max"] = lo, hi
    if typ in ("text", "longtext"):
        cap = LIM[typ]
        ml = raw.get("max_length", cap)
        if type(ml) is not int or not 1 <= ml <= cap:
            raise FormError("range", f"{where}: max_length 要 1–{cap}")
        q["max_length"] = ml
        if typ == "text" and raw.get("pattern") is not None:
            if raw["pattern"] not in PATTERNS:
                raise FormError("pattern", f"{where}: pattern 只能是 email / phone / url")
            q["pattern"] = raw["pattern"]
        ph = _txt(raw.get("placeholder"), lang, LIM["hint"], where + " placeholder")
        if ph:
            q["placeholder"] = ph
    if typ in ("number", "slider"):
        for k in ("min", "max", "step"):
            if raw.get(k) is not None:
                q[k] = _num(raw[k], f"{where} {k}")
        if typ == "slider" and ("min" not in q or "max" not in q):
            raise FormError("range", f"{where}: slider 要 min 和 max / needs min and max")
        if "min" in q and "max" in q and q["min"] >= q["max"]:
            raise FormError("range", f"{where}: min 要小于 max / min below max")
        if "step" in q and q["step"] <= 0:
            raise FormError("range", f"{where}: step 要大于 0")
        if typ == "slider" and (q["max"] - q["min"]) / q.get("step", 1) > 10_000:
            raise FormError("range", f"{where}: 刻度太多（≤ 10 000） / too many steps")
        u = _txt(raw.get("unit"), lang, LIM["unit"], where + " unit")
        if u:
            q["unit"] = u
        if typ == "slider" and raw.get("labels") is not None:
            ls = raw["labels"]
            if not isinstance(ls, list) or len(ls) != 2:
                raise FormError("shape", f"{where}: labels 要两端各一句 / two end labels")
            q["labels"] = [_txt(x, lang, 40, where + " labels") for x in ls]
    if typ in DATE_RE:
        for k in ("min", "max"):
            if raw.get(k) is not None:
                q[k] = _date(raw[k], typ, f"{where} {k}")
    if typ == "rating":
        sc = raw.get("scale", 5)
        if sc not in (5, 10):
            raise FormError("range", f"{where}: scale 只能是 5 或 10")
        q["scale"] = sc
    if typ == "file":
        acc = raw.get("accept", ["image", "audio", "file"])
        if not isinstance(acc, list) or not acc or not set(acc) <= set(ACCEPT):
            raise FormError("accept", f"{where}: accept 是 {' / '.join(ACCEPT)} 的组合")
        q["accept"] = [a for a in ACCEPT if a in acc]
        mf = raw.get("max_files", 3)
        if type(mf) is not int or not 1 <= mf <= FILES_MAX:
            raise FormError("range", f"{where}: max_files 要 1–{FILES_MAX}")
        q["max_files"] = mf
    if raw.get("show_if") is not None:
        q["show_if"] = _cond(raw["show_if"], prior, where)
    for k, v in (("default", raw.get("default")), ("rec", raw.get("recommend"))):
        if v is not None and typ != "note":
            q[k] = value_of(q, v, {}, where + " " + ("default" if k == "default" else "recommend"), files=None)
    return q


def _date(v, typ: str, where: str) -> str:
    if not isinstance(v, str) or not DATE_RE[typ].fullmatch(v):
        raise FormError("date", f"{where}: 格式 {DATE_FMT[typ]}")
    try:
        _dt.datetime.strptime(v, DATE_FMT[typ])
    except ValueError:
        raise FormError("date", f"{where}: 不是有效的{typ} / not a valid {typ}") from None
    return v


def value_of(q: dict, v, other: dict, where: str, files: dict | None):
    """One answer → its clean value; raises FormError(answer, …). `files`: bid → path (a submission), None = no files here
    (a default / a recommendation / a draft)."""
    t = q["type"]
    bad = lambda msg: FormError("answer", f"{where}: {msg}")   # noqa: E731
    if t in ("single", "buttons", "dropdown"):
        vals = [o["value"] for o in q["options"]] + ([OTHER] if q.get("other") else [])
        if v not in vals:
            raise bad("不是选项之一 / not one of the options")
        return v
    if t == "multi":
        vals = [o["value"] for o in q["options"]] + ([OTHER] if q.get("other") else [])
        if not isinstance(v, list) or len(set(map(str, v))) != len(v) or any(x not in vals for x in v):
            raise bad("要选项里的若干个 / some of the options")
        if not q.get("min", 0) <= len(v) <= q.get("max", len(vals)) and v:
            raise bad(f"要选 {q.get('min', 0)}–{q.get('max')} 个 / pick {q.get('min', 0)}–{q.get('max')}")
        return [x for x in vals if x in v]
    if t in ("text", "longtext"):
        if not isinstance(v, str):
            raise bad("要是文字 / text")
        v = _clean(v)
        if len(v) > q["max_length"]:
            raise bad(f"超过 {q['max_length']} 字 / over {q['max_length']} characters")
        if v and q.get("pattern") and not PATTERNS[q["pattern"]].fullmatch(v):
            raise bad({"email": "邮箱格式不对 / not an e-mail address", "phone": "电话格式不对 / not a phone number",
                       "url": "网址要以 http:// 或 https:// 开头 / a link starting with http(s)://"}[q["pattern"]])
        return v
    if t in ("number", "slider"):
        try:
            v = _num(v, where)
        except FormError:
            raise bad("要是数字 / a number") from None
        if ("min" in q and v < q["min"]) or ("max" in q and v > q["max"]):
            raise bad(f"要在 {q.get('min', '…')}–{q.get('max', '…')} 之间 / between {q.get('min', '…')} and {q.get('max', '…')}")
        return v
    if t in DATE_RE:
        try:
            v = _date(v, t, where)
        except FormError:
            raise bad(f"格式 {DATE_FMT[t]}") from None
        if ("min" in q and v < q["min"]) or ("max" in q and v > q["max"]):
            raise bad(f"要在 {q.get('min', '…')} 到 {q.get('max', '…')} 之间 / between {q.get('min', '…')} and {q.get('max', '…')}")
        return v
    if t == "rating":
        if type(v) is not int or not 1 <= v <= q["scale"]:
            raise bad(f"要 1–{q['scale']} / 1–{q['scale']}")
        return v
    if t == "rank":
        vals = [o["value"] for o in q["options"]]
        if not isinstance(v, list) or sorted(map(str, v)) != sorted(vals):
            raise bad("要把每个选项排一次 / every option once, in order")
        return list(v)
    if t == "file":
        if files is None:
            raise bad("附件题不能有默认值 / no default for attachments")
        if not isinstance(v, list) or not all(isinstance(b, str) for b in v) or len(set(v)) != len(v):
            raise bad("附件写错了 / malformed attachments")
        if len(v) > q["max_files"]:
            raise bad(f"最多 {q['max_files']} 个文件 / at most {q['max_files']} files")
        missing = [b for b in v if b not in files]
        if missing:
            raise bad("附件没随这次提交一起上来 / attachments missing from this submission")
        return list(v)
    raise bad("这一题不用答 / nothing to answer")


# ---------------------------------------------------------------- the form
def _questions_of(raw: dict) -> list:
    return [q for s in raw.get("sections") or [] if isinstance(s, dict) for q in (s.get("questions") or [])] \
        if raw.get("sections") is not None else list(raw.get("questions") or [])


def normalize(raw, lang: str = "zh", mode: str | None = None) -> tuple[dict, list[str]]:
    """The Agent's JSON → (spec, reasons it became a Deep Dive). Raises FormError(why, detail) — detail names the field
    in both languages so the Agent can fix it."""
    if not isinstance(raw, dict):
        raise FormError("shape", "表单要是一个 JSON 对象 / a form is one JSON object")
    if len(json.dumps(raw, ensure_ascii=False).encode()) > JSON_MAX:
        raise FormError("too_big", f"整份 JSON 超过 {JSON_MAX // 1024} KB / over {JSON_MAX // 1024} KB")
    if raw.get("schema") not in (None, SCHEMA):
        raise FormError("schema", f"schema 要是 {SCHEMA}")
    lang = raw.get("lang") if raw.get("lang") in ("zh", "en") else lang
    asked = mode or raw.get("mode") or "auto"
    if asked not in ("light", "deep", "auto"):
        raise FormError("mode", "mode 只能是 light / deep / auto")
    spec: dict = {"schema": SCHEMA, "lang": lang, "title": _txt(raw.get("title"), lang, LIM["title"], "title", True)}
    for k, lim in (("intro", LIM["intro"]), ("submit_label", LIM["submit"])):
        v = _txt(raw.get(k), lang, lim, k)
        if v:
            spec[k] = v
    if raw.get("sections") is not None and raw.get("questions") is not None:
        raise FormError("shape", "sections 和 questions 只写一个 / sections or questions, not both")
    secs_raw = raw.get("sections")
    if secs_raw is None:
        secs_raw = [{"id": "main", "questions": raw.get("questions")}]
    if not isinstance(secs_raw, list) or not 1 <= len(secs_raw) <= DEEP_MAX_SECTIONS:
        raise FormError("sections", f"要 1–{DEEP_MAX_SECTIONS} 节 / 1–{DEEP_MAX_SECTIONS} sections")
    rec = raw.get("recommend") or {}
    if not isinstance(rec, dict):
        raise FormError("shape", "recommend 要是 {题 id: 推荐答案} / {question id: answer}")
    prior: dict = {}
    sections, sids = [], set()
    for i, s in enumerate(secs_raw):
        where = f"第 {i + 1} 节 / section {i + 1}"
        if not isinstance(s, dict) or not isinstance(s.get("questions"), list) or not s["questions"]:
            raise FormError("sections", f"{where}: 要有 questions / needs questions")
        sid = s.get("id", f"s{i + 1}")
        if not isinstance(sid, str) or not ID_RE.fullmatch(sid) or sid in sids:
            raise FormError("id", f"{where}: 节 id 要唯一、[a-z0-9_] / unique section id")
        sids.add(sid)
        sec = {"id": sid, "questions": []}
        for k, lim in (("title", LIM["section"]), ("intro", LIM["intro"])):
            v = _txt(s.get(k), lang, lim, f"{where} {k}")
            if v:
                sec[k] = v
        for j, qr in enumerate(s["questions"]):
            if isinstance(qr, dict) and qr.get("id") in rec and "recommend" not in qr:
                qr = {**qr, "recommend": rec[qr["id"]]}
            q = _question(qr, lang, prior, f"{where} 第 {j + 1} 题 / question {j + 1}")
            prior[q["id"]] = q
            sec["questions"].append(q)
        sections.append(sec)
    unknown = [k for k in rec if k not in prior]
    if unknown:
        raise FormError("recommend", f"recommend 里有不存在的题：{', '.join(map(str, unknown[:5]))} / unknown question ids")
    qs = list(prior.values())
    answerable = [q for q in qs if q["type"] != "note"]
    if not answerable:
        raise FormError("empty", "至少要有一道要答的题 / at least one question to answer")
    if len(answerable) > DEEP_MAX_Q:
        raise FormError("too_many", f"最多 {DEEP_MAX_Q} 题 / at most {DEEP_MAX_Q} questions")
    spec["sections"] = sections
    if raw.get("preselect") is True:
        spec["preselect"] = True
    _secret_free([spec["title"], spec.get("intro", ""), *[s.get("title", "") + "\n" + s.get("intro", "") for s in sections],
                  *[json.dumps(q, ensure_ascii=False) for q in qs]])
    reasons = []
    if len(sections) > 1:
        reasons.append(_pick(lang, f"{len(sections)} 节", f"{len(sections)} sections"))
    if len(answerable) > LIGHT_MAX_Q:
        reasons.append(_pick(lang, f"{len(answerable)} 题", f"{len(answerable)} questions"))
    if any(q["type"] == "file" for q in qs):
        reasons.append(_pick(lang, "带附件", "attachments"))
    if any(q["type"] == "rank" for q in qs):
        reasons.append(_pick(lang, "排序题", "a ranking"))
    if sum(q["type"] == "longtext" for q in qs) > 1:
        reasons.append(_pick(lang, "多道长文本", "several long answers"))
    if len(json.dumps(spec, ensure_ascii=False).encode()) > LIGHT_SPEC_MAX:
        reasons.append(_pick(lang, "内容太长", "too long"))
    if asked == "deep":
        spec["mode"], reasons = "deep", []
    else:
        spec["mode"] = "deep" if reasons else "light"
    if spec["mode"] == "light":
        spec.pop("preselect", None)          # Quick Pick never pre-selects: a slip of the finger would submit
        if len(answerable) == 1 and answerable[0]["type"] in TAP_TYPES and not answerable[0].get("other"):
            spec["tap"] = True
    spec["n"] = len(answerable)
    return spec, reasons


def questions(spec: dict) -> list[dict]:
    return [q for s in spec["sections"] for q in s["questions"]]


def check(spec: dict, answers, other=None, files: dict | None = None, draft: bool = False) -> tuple[dict, list[str]]:
    """A submission (or a draft: nothing required, unknown files ignored) → (clean answers, skipped ids). Hidden questions
    are dropped and never checked. Raises FormError(answer | required, detail)."""
    if not isinstance(answers, dict) or (other is not None and not isinstance(other, dict)):
        raise FormError("shape", "answers 要是 {题 id: 答案}")
    other = other or {}
    qs = {q["id"]: q for q in questions(spec)}
    extra = [k for k in answers if k not in qs]
    if extra:
        raise FormError("answer", "答案里有不存在的题 / unknown question ids")
    clean, skipped, oth = {}, [], {}
    for q in qs.values():
        if q["type"] == "note":
            continue
        if not holds(q.get("show_if"), clean):
            skipped.append(q["id"])
            continue
        v = answers.get(q["id"])
        if _empty(v):
            if q.get("required") and not draft:
                raise FormError("required", f"「{q['label'][:40]}」必填 / is required")
            skipped.append(q["id"])
            continue
        if q["type"] == "file" and draft:
            continue
        clean[q["id"]] = value_of(q, v, other, f"「{q['label'][:40]}」", files)
        picked = clean[q["id"]] if isinstance(clean[q["id"]], list) else [clean[q["id"]]]
        if q.get("other") and OTHER in picked:
            o = other.get(q["id"])
            if not isinstance(o, str) or not _clean(o):
                if not draft:
                    raise FormError("required", f"「{q['label'][:40]}」选了「其他」要写上内容 / “Other” needs text")
            else:
                if len(_clean(o)) > LIM["other"]:
                    raise FormError("answer", f"「{q['label'][:40]}」其他：超过 {LIM['other']} 字")
                oth[q["id"]] = _clean(o)
        if q["type"] == "multi" and not draft and len(clean[q["id"]]) < q.get("min", 0):
            raise FormError("answer", f"「{q['label'][:40]}」至少选 {q['min']} 个 / pick at least {q['min']}")
    if files is not None:
        used = [b for q in qs.values() if q["type"] == "file" for b in clean.get(q["id"], [])]
        if sorted(used) != sorted(files):
            raise FormError("answer", "附件和题目对不上 / attachments do not match the questions")
    if oth:
        clean["_other"] = oth
    return clean, skipped


def label_of(q: dict, v, other: dict, lang: str) -> str:
    t = q["type"]
    opts = {o["value"]: o["label"] for o in q.get("options", [])}
    oth = _pick(lang, "其他", "Other") + (("：" if lang == "zh" else ": ") + other.get(q["id"], "") if other.get(q["id"]) else "")
    if t in ("single", "buttons", "dropdown"):
        return oth if v == OTHER else opts.get(v, str(v))
    if t == "multi":
        return ("、" if lang == "zh" else ", ").join(oth if x == OTHER else opts.get(x, str(x)) for x in v)
    if t == "rank":
        return " > ".join(opts.get(x, str(x)) for x in v)
    if t in ("number", "slider"):
        return f"{v}{(' ' if lang == 'en' else '') + q['unit'] if q.get('unit') else ''}"
    if t == "rating":
        return f"{v} / {q['scale']}"
    if t == "file":
        return _pick(lang, f"{len(v)} 个文件", f"{len(v)} file(s)")
    return str(v)


def rows(spec: dict, clean: dict, lang: str, cut: int | None = ROW_MAX) -> list[list]:
    """[[label, answer text, recommended?]] in order — the read-only view on the phone and the Agent's lines."""
    other = clean.get("_other") or {}
    out = []
    for q in questions(spec):
        if q["id"] in clean and q["id"] != "_other":
            txt = label_of(q, clean[q["id"]], other, lang)
            if cut and len(txt) > cut:
                txt = txt[:cut - 1] + "…"
            out.append([q["label"][:cut] if cut else q["label"], txt, "rec" in q and q["rec"] == clean[q["id"]]])
    return out


def agent_text(rec: dict, clean: dict, skipped: list, lang: str, answer_path: str | None) -> str:
    spec = rec["spec"]
    n = len([q for q in questions(spec) if q["type"] != "note" and q["id"] not in skipped])
    total = spec["n"]
    kind = _pick(lang, "快选" if spec["mode"] == "light" else "深聊卡", "Quick Pick" if spec["mode"] == "light" else "Deep Dive")
    head = _pick(lang, f"〔表单回复 · {rec['id']} · {kind} · {spec['title']} · {n}/{total} 题〕",
                 f"[Form answer · {rec['id']} · {kind} · {spec['title']} · {n}/{total} answered]")
    lines = [head]
    for i, (label, txt, r) in enumerate(rows(spec, clean, lang, cut=None), 1):
        if "\n" in txt:
            txt = "\n   " + txt.replace("\n", "\n   ")
        lines.append(f"{i}. {label} → {txt}" + (_pick(lang, "（接受推荐）", " (accepted the recommendation)") if r else ""))
    if skipped:
        lines.append(_pick(lang, f"没答 / 未显示：{len(skipped)} 题", f"Not answered / not shown: {len(skipped)}"))
    if answer_path:
        lines.append(_pick(lang, f"完整答案 JSON：{answer_path}（也可 agentj ask show {rec['id']} --json）",
                           f"Full answer JSON: {answer_path} (or agentj ask show {rec['id']} --json)"))
    lines.append(_pick(lang, "这是主人本人在手机上填的答案；按它继续，不要再把同样的问题问一遍。",
                       "These are the owner's own answers from the phone; carry on with them — don't ask the same questions again."))
    return "\n".join(lines)


def answer_json(rec: dict, clean: dict, skipped: list, lang: str, paths: dict) -> dict:
    spec = rec["spec"]
    other = clean.get("_other") or {}
    ans = {}
    for q in questions(spec):
        if q["id"] not in clean:
            continue
        v = clean[q["id"]]
        a: dict = {"value": v} if q["type"] != "file" else {"files": [paths.get(b, "") for b in v]}
        if q["type"] in ("single", "buttons", "dropdown", "multi", "rank"):
            a["label"] = label_of(q, v, other, lang)
        if q["id"] in other:
            a["other"] = other[q["id"]]
        if "rec" in q:
            a["recommended"] = q["rec"] == v
        ans[q["id"]] = a
    return {"schema": ANSWER_SCHEMA, "form": rec["id"], "rev": rec.get("rev", 1), "template": rec.get("template"),
            "title": spec["title"], "mode": spec["mode"], "submitted_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "device": rec.get("by"), "answers": ans, "skipped": skipped}


# ---------------------------------------------------------------- templates
def _subst(v, vals: dict):
    if isinstance(v, str):
        return re.sub(r"\{\{([a-z0-9_]{1,40})\}\}", lambda m: str(vals.get(m.group(1), "")), v)
    if isinstance(v, list):
        return [_subst(x, vals) for x in v]
    if isinstance(v, dict):
        return {k: _subst(x, vals) for k, x in v.items()}
    return v


def _template_file(name: str) -> pathlib.Path:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name):
        raise FormError("template", "模板名只有小写字母、数字、连字符 / lowercase, digits, hyphens")
    p = TEMPLATE_DIR / f"{name}.json"
    if not p.is_file():
        raise FormError("template", f"没有模板 {name}（agentj ask templates 列出全部） / no such template")
    return p


def template(name: str) -> dict:
    return json.loads(_template_file(name).read_text(encoding="utf-8"))


def example(name: str, lang: str = "zh") -> dict:
    """The template's filled-in sample in that language (the gallery's and the tests' fill)."""
    ex = template(name).get("example") or {}
    return dict(ex.get(lang) or ex.get("zh") or {}) if set(ex) <= {"zh", "en"} else dict(ex)


def templates() -> list[dict]:
    out = []
    for p in sorted(TEMPLATE_DIR.glob("*.json")):
        with contextlib.suppress(OSError, ValueError):
            t = json.loads(p.read_text(encoding="utf-8"))
            out.append({"name": t["name"], "mode": t["mode"], "title": t["title"], "about": t["about"],
                        "vars": {k: {"label": v.get("label"), "required": v.get("required", False)} for k, v in t.get("vars", {}).items()},
                        "fill": t.get("fill", [])})
    return out


def apply(name: str, fill: dict | None) -> dict:
    """A template + the Agent's fill → a raw form (still to be normalized). Only the template's `vars` and its `fill`
    paths may change: title, intro, submit_label, options.<id>, recommend.<id>, label.<id>, hint.<id>, default.<id>, drop."""
    t = template(name)
    fill = dict(fill or {})
    vars_ = t.get("vars", {})
    vals = {}
    for k, spec in vars_.items():
        v = fill.pop(k, spec.get("default"))
        if v is None or v == "":
            if spec.get("required"):
                raise FormError("fill", f"模板 {name} 要填 {k}（{loc(spec.get('label'), 'zh')}） / needs {k}")
            v = ""
        if not isinstance(v, (str, int, float)) or isinstance(v, bool) or len(str(v)) > 500:
            raise FormError("fill", f"{k} 要是 ≤ 500 字的文字 / text up to 500 characters")
        vals[k] = v
    form = _subst(copy.deepcopy(t["form"]), vals)
    allowed = set(t.get("fill", []))
    qs = {q.get("id"): q for q in _questions_of(form) if isinstance(q, dict)}

    def ok(path: str) -> bool:
        return path in allowed or path.split(".")[0] + ".*" in allowed
    for k, v in fill.items():
        if not ok(k):
            raise FormError("fill_field", f"模板 {name} 不能改 {k}；能改的：{', '.join(sorted(allowed | set(vars_))) or '无'}"
                                          f" / {k} is not fillable")
        head, _, qid = k.partition(".")
        if head in ("title", "intro", "submit_label") and not qid:
            form[head] = v
        elif head == "drop" and not qid:
            if not isinstance(v, list) or any(x not in qs or qs[x].get("keep") for x in v):
                raise FormError("fill_field", "drop 只能去掉模板里允许去掉的题 / only optional template questions")
            for s in form["sections"] if form.get("sections") is not None else [form]:
                s["questions"] = [q for q in s["questions"] if q.get("id") not in v]
        elif head in ("options", "recommend", "label", "hint", "default") and qid in qs:
            qs[qid][head] = v
        else:
            raise FormError("fill_field", f"{k}：没有这道题 / no such question")
    for q in _questions_of(form):
        if isinstance(q, dict):
            q.pop("keep", None)
    form.setdefault("mode", t["mode"])
    return form


# ---------------------------------------------------------------- the store (host)
class Store:
    def __init__(self, st):
        self.st = st
        self.dir = pathlib.Path(st.root) / "forms"

    def _p(self, fid: str, ext: str = "json") -> pathlib.Path:
        if not isinstance(fid, str) or not FORM_ID.fullmatch(fid):
            raise FormError("id", "表单 id 不对 / bad form id")
        return self.dir / f"{fid}.{ext}"

    def save(self, rec: dict) -> None:
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.st.write_private(self._p(rec["id"]), json.dumps(rec, ensure_ascii=False).encode())

    def get(self, fid: str) -> dict | None:
        try:
            rec = json.loads(self._p(fid).read_text(encoding="utf-8"))
        except (OSError, ValueError, FormError):
            return None
        if rec.get("state") == "open" and rec.get("expires", 0) < time.time():
            rec["state"] = "expired"
        return rec

    def all(self) -> list[dict]:
        out = []
        if self.dir.is_dir():
            for p in self.dir.glob("f_*.json"):
                if FORM_ID.fullmatch(p.stem):
                    r = self.get(p.stem)
                    if r:
                        out.append(r)
        return sorted(out, key=lambda r: r.get("created", 0), reverse=True)

    def draft(self, fid: str) -> dict | None:
        with contextlib.suppress(OSError, ValueError, FormError):
            return json.loads(self._p(fid, "draft").read_text(encoding="utf-8"))
        return None

    def save_draft(self, fid: str, d: dict) -> None:
        self.st.write_private(self._p(fid, "draft"), json.dumps(d, ensure_ascii=False).encode())

    def drop_draft(self, fid: str) -> None:
        with contextlib.suppress(OSError, FormError):
            self._p(fid, "draft").unlink()

    def prune(self) -> None:
        recs = self.all()
        for r in recs[KEEP:]:
            for ext in ("json", "draft"):
                with contextlib.suppress(OSError, FormError):
                    self._p(r["id"], ext).unlink()


def view(rec: dict) -> dict:
    """What a page carries (turn.form): enough to draw the inline block; a Quick Pick carries its whole spec."""
    spec = rec["spec"]
    v = {"id": rec["id"], "mode": spec["mode"], "title": spec["title"], "n": spec["n"], "state": rec["state"],
         "exp": int(rec["expires"] * 1000), "secs": len(spec["sections"])}
    if spec["mode"] == "light":
        v["spec"] = spec
    if spec.get("tap"):
        v["tap"] = True
    if rec.get("files"):
        v["files"] = rec["files"]
    if rec["state"] == "answered":
        v.update(at=int(rec.get("at", 0) * 1000), by=(rec.get("by") or "")[:64], rows=rec.get("rows", [])[:DEEP_MAX_Q])
    return v


class FormService:
    """serve's side: `agentj ask` over elevate.sock, `form_get` / `form_draft` from the phone, `say.form` submissions."""

    def __init__(self, host):
        self.host = host
        self.st = host.st
        self.store = Store(host.st)

    def lang(self) -> str:
        return "en" if getattr(self.host, "lang", "zh") == "en" else "zh"

    def _page(self, rec: dict) -> None:
        if rec.get("turn") is not None:
            self.host.hist_update(rec["turn"], form=view(rec))

    # ------------------------------------------------------------ the Agent
    async def agent(self, req: dict) -> dict:
        op = req.get("op")
        if op == "send":
            return self.send(req)
        if op == "list":
            return {"ok": True, "forms": [{"id": r["id"], "title": r["spec"]["title"], "mode": r["spec"]["mode"], "state": r["state"],
                                          "n": r["spec"]["n"], "created": int(r["created"]), "expires": int(r["expires"]),
                                          "template": r.get("template")} for r in self.store.all()[:50]]}
        rec = self.store.get(req.get("id")) if isinstance(req.get("id"), str) else None
        if op in ("show", "cancel") and rec is None:
            return {"ok": False, "result": "not_found"}
        if op == "show":
            return {"ok": True, "id": rec["id"], "state": rec["state"], "title": rec["spec"]["title"], "mode": rec["spec"]["mode"],
                    "answer": rec.get("answer"), "spec": rec["spec"] if req.get("spec") else None}
        if op == "cancel":
            if rec["state"] != "open":
                return {"ok": False, "result": rec["state"]}
            rec["state"] = "cancelled"
            self.store.save(rec)
            self.store.drop_draft(rec["id"])
            self._page(rec)
            self.st.log("form_cancel", id=rec["id"])
            return {"ok": True, "id": rec["id"], "result": "cancelled"}
        return {"ok": False, "result": "refused", "why": "shape", "detail": "op: send | list | show | cancel"}

    def send(self, req: dict) -> dict:
        if not FORMS_RELEASED:
            return {"ok": False, "result": "refused", "why": "pending_owner_decision", "detail": "互动表单尚待确认后启用 / Interactive forms await the owner decision"}
        from . import provenance
        if self.host.stopped():
            return {"ok": False, "result": "stopped"}
        if not provenance.owner_speaking(getattr(self.host, "agent", None)):
            return {"ok": False, "result": "group_source", "detail": provenance.outsider_detail()}
        try:
            spec, converted = normalize(req.get("form"), self.lang(), req.get("mode"))
        except FormError as e:
            self.st.log("form_refused", why=e.why)
            return {"ok": False, "result": "refused", "why": e.why, "detail": e.detail}
        ttl = req.get("ttl", TTL[spec["mode"]])
        if type(ttl) is not int or not TTL_MIN <= ttl <= TTL_MAX:
            return {"ok": False, "result": "refused", "why": "ttl", "detail": "ttl 要 1 小时–30 天 / 1 hour – 30 days"}
        now = time.time()
        tpl = req.get("template") if isinstance(req.get("template"), str) and re.fullmatch(r"[a-z0-9-]{1,41}", req["template"]) else None
        rec = {"id": "f_" + secrets.token_hex(8), "rev": 1, "spec": spec, "state": "open", "created": now, "expires": now + ttl,
               "template": tpl, "turn": None}
        h = self.host
        tid = h.cur_turn if getattr(h, "cur_turn", None) is not None else None
        t = h.hist.get(tid) if tid is not None else None
        if t is not None and t.get("end") == "open" and not t.get("form"):
            rec["turn"] = tid
            self.store.save(rec)
            h.hist_update(tid, form=view(rec))
        else:
            self.store.save(rec)
            rec["turn"] = h.hist_add(h._agent_src(), "", "done", form=view(rec))["id"]
            self.store.save(rec)
        self.store.prune()
        self.st.log("form_sent", id=rec["id"], kind=spec["mode"], count=spec["n"])
        return {"ok": True, "id": rec["id"], "mode": spec["mode"], "n": spec["n"], "ttl": ttl, "converted": converted,
                "tap": bool(spec.get("tap")), "turn": rec["turn"]}

    # ------------------------------------------------------------ the phone
    async def on_phone(self, s, obj: dict) -> None:
        from . import wire
        t, fid = obj.get("t"), obj.get("id")
        if t == "form_get":
            r = obj.get("r")
            if not wire.is_rid(r):
                return
            rec = self.store.get(fid) if isinstance(fid, str) else None
            if rec is None:
                return await self.host.send_app(s, {"t": "form_res", "r": r, "ok": False, "why": "gone"})
            out = {"t": "form_res", "r": r, "ok": True, "id": rec["id"], "state": rec["state"], "spec": rec["spec"],
                   "exp": int(rec["expires"] * 1000)}
            if rec["state"] == "open":
                d = self.store.draft(rec["id"])
                if d:
                    out["draft"] = d
            elif rec["state"] == "answered":
                out.update(rows=rec.get("rows", []), at=int(rec.get("at", 0) * 1000), by=rec.get("by") or "")
            return await self.host.send_app(s, out)
        if t == "form_draft":
            rec = self.store.get(fid) if isinstance(fid, str) else None
            ok, why = rec is not None and rec["state"] == "open", None
            if ok:
                try:
                    if len(json.dumps(obj.get("answers"), ensure_ascii=False).encode()) > JSON_MAX:
                        raise FormError("too_big")
                    clean, _ = check(rec["spec"], obj.get("answers") or {}, obj.get("other"), draft=True)
                except FormError as e:
                    ok, why = False, e.why
                else:
                    sec = obj.get("sec") if type(obj.get("sec")) is int and 0 <= obj["sec"] < DEEP_MAX_SECTIONS else 0
                    other = clean.pop("_other", {})
                    self.store.save_draft(rec["id"], {"answers": clean, "other": other, "sec": sec, "at": int(time.time() * 1000),
                                                      "by": (s.name or s.device)[:64]})
            else:
                why = "gone" if rec is None else rec["state"]
            return await self.host.send_app(s, {"t": "form_draft_res", "id": fid if isinstance(fid, str) else "", "ok": ok,
                                                **({"why": why} if why else {"at": int(time.time() * 1000)})})

    def take(self, form, att) -> dict:
        """A say's `form` before its attachments are claimed → the open record (or FormError: gone | expired | answered |
        cancelled | shape | answer | required). Synchronous: nothing else runs between this and claim + accept()."""
        if not isinstance(form, dict) or not isinstance(form.get("id"), str) or not isinstance(form.get("answers"), dict):
            raise FormError("shape")
        rec = self.store.get(form["id"])
        if rec is None:
            raise FormError("gone")
        if rec["state"] != "open":
            raise FormError(rec["state"])
        from . import wire
        if att is not None and (not isinstance(att, list) or not all(wire.is_id22(b) for b in att) or len(set(att)) != len(att)):
            raise FormError("shape")
        bids = {b: "" for b in (att or [])}
        check(rec["spec"], form["answers"], form.get("other"), files=bids)
        return rec

    def accept(self, s, rec: dict, form: dict, blobs: list, workdir: str | None) -> str:
        """After claim: the clean answers (paths filled in), the answer JSON in the Agent's inbox, the record answered and the
        form's page updated. → the Agent's text (without the attachment block, which compose.render adds)."""
        lang = rec["spec"].get("lang") or self.lang()
        paths = {b.bid: b.path for b in blobs}
        clean, skipped = check(rec["spec"], form["answers"], form.get("other"), files=paths)
        rec.update(state="answered", at=time.time(), by=(s.name or s.device)[:64], rows=rows(rec["spec"], clean, lang),
                   files=len(blobs))
        aj = answer_json(rec, clean, skipped, lang, paths)
        rec["answer"] = aj
        path = None
        if workdir:
            try:
                path = self._place(workdir, rec["id"], aj)
            except Exception:  # noqa: BLE001 — the answer still reaches the Agent as text; `agentj ask show` has the JSON
                self.st.log("form_answer_file", id=rec["id"], result="failed")
        rec["answer_path"] = path
        self.store.save(rec)
        self.store.drop_draft(rec["id"])
        self._page(rec)
        self.st.log("form_answered", id=rec["id"], device=s.device, count=len(clean), bytes=len(blobs))
        return agent_text(rec, clean, skipped, lang, path)

    def _place(self, workdir: str, fid: str, aj: dict) -> str:
        from . import inbox
        tmp = self.store.dir / f".{fid}.answer.tmp"
        self.st.write_private(tmp, json.dumps(aj, ensure_ascii=False, indent=1).encode())
        try:
            return inbox.place(workdir, str(tmp), f"form-{fid}.json", "application/json", secrets.token_urlsafe(16))
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def reopen(self, fid: str) -> None:
        """A withdrawn (still queued) answer: the form is open again, as it was."""
        rec = self.store.get(fid)
        if rec is None or rec["state"] != "answered":
            return
        for k in ("at", "by", "rows", "answer", "answer_path", "files"):
            rec.pop(k, None)
        rec["state"] = "open" if rec["expires"] > time.time() else "expired"
        self.store.save(rec)
        self._page(rec)
        self.st.log("form_reopen", id=fid)


# ---------------------------------------------------------------- CLI (runs as the Agent): agentj ask (alias agentj form)
def _out(obj, as_json: bool, text: str) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1) if as_json else text)


def _ttl(s: str | None) -> int | None:
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,4})([hd])", s.strip())
    if not m:
        raise FormError("ttl", "--ttl 写成 12h 或 7d / like 12h or 7d")
    return int(m.group(1)) * (3600 if m.group(2) == "h" else 86400)


def _read_json(path: str):
    data = sys.stdin.read() if path == "-" else pathlib.Path(path).expanduser().read_text(encoding="utf-8")
    try:
        return json.loads(data)
    except ValueError as e:
        raise FormError("json", f"{path} 不是有效的 JSON：{e} / not valid JSON") from None


def build(a) -> tuple[dict, str | None]:
    """--file / --template + --fill + --set → (raw form, template name)."""
    if a.template:
        fill = _read_json(a.fill) if a.fill else {}
        if not isinstance(fill, dict):
            raise FormError("fill", "--fill 要是一个 JSON 对象 / one JSON object")
        for kv in a.set or []:
            k, eq, v = kv.partition("=")
            if not eq:
                raise FormError("fill", "--set 写成 名=值 / name=value")
            try:
                fill[k.strip()] = json.loads(v) if v[:1] in "[{\"" or v in ("true", "false") else v
            except ValueError:
                fill[k.strip()] = v
        return apply(a.template, fill), a.template
    if not a.file:
        raise FormError("shape", "要 --file form.json 或 --template <名>（agentj ask templates） / --file or --template")
    return _read_json(a.file), None


def cmd(a) -> int:
    lang = "en" if a.en else "zh"
    J = a.json
    try:
        if a.action == "templates":
            ts = templates()
            _out(ts, J, "\n".join(f"{t['name']:<20} {'快选' if t['mode'] == 'light' else '深聊卡'}  {loc(t['title'], lang)} — "
                                  f"{loc(t['about'], lang)}" for t in ts) + "\n\n" +
                 _pick(lang, "用法：agentj ask --template <名> --fill fill.json（agentj ask template <名> 看题目与可填字段）",
                       "Use: agentj ask --template <name> --fill fill.json (agentj ask template <name> shows its fields)"))
            return 0
        if a.action == "template":
            t = template(a.target or "")
            if J:
                _out(t, True, "")
                return 0
            print(f"{t['name']} · {'Quick Pick 快选' if t['mode'] == 'light' else 'Deep Dive 深聊卡'} · {loc(t['title'], lang)}")
            print(loc(t["about"], lang))
            print(_pick(lang, "要填的变量：", "Variables:"))
            for k, v in t.get("vars", {}).items():
                print(f"  {k}{' *' if v.get('required') else ''}  {loc(v.get('label'), lang)}")
            print(_pick(lang, "还能改：", "Also fillable:"), ", ".join(t.get("fill", [])) or "-")
            print(_pick(lang, "示例 fill：", "Example fill:"), json.dumps(example(t["name"], lang), ensure_ascii=False))
            return 0
        if a.action in ("list", "show", "cancel"):
            from . import elevate, state
            if a.action != "list" and not a.target:
                raise FormError("shape", f"agentj ask {a.action} <表单 id>")
            res = elevate.client_request(state.State(), {"t": "form", "op": a.action, "id": a.target, "spec": bool(J)}, timeout=30)
            return _report(res, a, lang)
        raw, tpl = build(a)
        mode = a.mode if a.mode != "auto" else None
        if a.action == "validate":
            spec, conv = normalize(raw, lang, mode)
            _out({"ok": True, "mode": spec["mode"], "n": spec["n"], "converted": conv, "spec": spec}, J,
                 _pick(lang, f"合格：{'快选' if spec['mode'] == 'light' else '深聊卡'}，{spec['n']} 题", f"Valid: {spec['mode']}, "
                       f"{spec['n']} questions") + (_pick(lang, f"（会转为深聊卡：{'、'.join(conv)}）", f" (becomes a Deep Dive: {', '.join(conv)})") if conv else ""))
            return 0
        normalize(raw, lang, mode)                  # the Agent's own mistakes are named before anything is sent
        from . import elevate, state
        req = {"t": "form", "op": "send", "form": raw, "mode": mode, "template": tpl}
        ttl = _ttl(a.ttl)
        if ttl is not None:
            req["ttl"] = ttl
        return _report(elevate.client_request(state.State(), req, timeout=30), a, lang)
    except FormError as e:
        _out({"ok": False, "why": e.why, "detail": e.detail}, J, _pick(lang, "表单不合格：", "Form refused: ") + (e.detail or e.why))
        return 2
    except OSError as e:
        _out({"ok": False, "why": "file", "detail": str(e)}, J, _pick(lang, "读不了文件：", "Cannot read: ") + str(e))
        return 2


MSG = {
    "unavailable": ("Agent J 没在运行（agentj service status）。", "Agent J is not running (agentj service status)."),
    "stopped": ("已急停，没有发表单。", "Agent J is stopped; no form was sent."),
    "group_source": ("这一轮来自群成员 / 访客，不是主人本人，不能给主人发表单。", "This turn came from a group member or guest, not the owner; no form was sent."),
    "not_found": ("没有这张表单（id 不对，或已被清理）。", "No such form (wrong id, or pruned)."),
}


def _report(res: dict, a, lang: str) -> int:
    J = a.json
    if not res.get("ok"):
        r = res.get("result")
        if r in MSG:
            msg = MSG[r][lang == "en"]
        elif r == "refused":
            msg = _pick(lang, "表单不合格：", "Form refused: ") + (res.get("detail") or res.get("why") or "")
        else:
            msg = _pick(lang, f"没有完成：{r}", f"Not done: {r}")
        _out(res, J, msg)
        return 75 if res.get("result") == "unavailable" else 2
    if a.action == "list":
        fs = res.get("forms", [])
        _out(res, J, "\n".join(f"{f['id']}  {f['state']:<9} {f['mode']:<5} {f['n']:>2}  {f['title']}" for f in fs) or
             _pick(lang, "还没有表单。", "No forms yet."))
        return 0
    if a.action == "show":
        ans = res.get("answer")
        if J:
            _out(res, True, "")
        elif ans:
            print(f"{res['id']} · {res['title']} · {res['state']}")
            for k, v in ans["answers"].items():
                print(f"  {k}: {v.get('label', v.get('value', v.get('files')))}")
        else:
            print(f"{res['id']} · {res['title']} · {res['state']}" + _pick(lang, "（还没有答案）", " (no answer yet)"))
        return 0
    if a.action == "cancel":
        _out(res, J, _pick(lang, f"已撤回 {res['id']}。", f"Withdrawn: {res['id']}."))
        return 0
    days = res["ttl"] // 86400
    left = _pick(lang, f"{days} 天有效" if days else f"{res['ttl'] // 3600} 小时有效", f"valid {days} days" if days else f"valid {res['ttl'] // 3600} h")
    kind = _pick(lang, "快选" if res["mode"] == "light" else "深聊卡", "Quick Pick" if res["mode"] == "light" else "Deep Dive")
    conv = res.get("converted") or []
    _out(res, J, _pick(lang, f"已发出 {kind} {res['id']}（{res['n']} 题，{left}）", f"Sent {kind} {res['id']} ({res['n']} questions, {left})")
         + (_pick(lang, f"。已转为深聊卡（原因：{'、'.join(conv)}）", f". Became a Deep Dive ({', '.join(conv)})") if conv else "")
         + _pick(lang, "。现在结束这一回合即可：主人填完，答案会作为一条新消息进来。",
                 ". End this turn now: when the owner submits, the answers arrive as a new message."))
    return 0


def add_parser(sub, *, include_ask=True) -> None:
    if include_ask:
        _ask_parser(sub)
    f = sub.add_parser("form", help="= agentj ask", description=DESCRIPTION)
    _args(f)


def _ask_parser(sub):
    p = sub.add_parser("ask", help="互动表单：快选 / 深聊卡（回复卡里点选、全屏问卷） / interactive forms for the owner",
                       description=DESCRIPTION)
    _args(p)


DESCRIPTION = ("给主人发一张互动表单（同一份 agentj.form/v1 JSON）：≤ 5 题、无附件画在回复卡里（快选），否则是全屏的深聊卡。"
               "优先套模板：agentj ask templates。答案作为主人的一条消息回来。 / Send the owner an interactive form; prefer a "
               "template (agentj ask templates). Answers come back as the owner's message.")


def _args(p) -> None:
    p.add_argument("action", nargs="?", default="send", choices=["send", "templates", "template", "validate", "list", "show", "cancel"])
    p.add_argument("target", nargs="?", help="template：模板名 · show / cancel：表单 id")
    p.add_argument("--file", help="表单 JSON（- = 标准输入） / the form JSON (- = stdin)")
    p.add_argument("--template", help="套用模板 / use a template")
    p.add_argument("--fill", help="模板的填充 JSON / the template's fill JSON")
    p.add_argument("--set", action="append", help="模板变量 名=值（可多次） / name=value (repeatable)")
    p.add_argument("--mode", choices=["auto", "light", "deep"], default="auto")
    p.add_argument("--ttl", help="有效期，如 12h / 7d（1 小时–30 天） / validity, e.g. 12h / 7d")
    p.add_argument("--en", action="store_true", help="English messages")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: sys.exit(cmd(a)))
