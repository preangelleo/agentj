"""零依赖 JSON Schema 校验（覆盖本包 schema 用到的 draft 2020-12 子集）。

支持：type / enum / const / minLength / maxLength / pattern / minimum / maximum / required /
properties / additionalProperties / items / minItems / maxItems / oneOf / anyOf。
装了 `jsonschema` 时测试会再用官方实现交叉校验。
"""
from __future__ import annotations

import re

TYPES = {"string": str, "object": dict, "array": list, "boolean": bool, "null": type(None)}


def _is_type(v, t):
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, TYPES[t])


def validate(v, schema: dict, path: str = "$") -> list[str]:
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_is_type(v, x) for x in ts):
            return [f"{path}: 应为 {'/'.join(ts)}"]
    if "enum" in schema and v not in schema["enum"]:
        errs.append(f"{path}: 只能是 {schema['enum']}")
    if "const" in schema and v != schema["const"]:
        errs.append(f"{path}: 必须等于 {schema['const']!r}")
    if isinstance(v, str):
        if len(v) < schema.get("minLength", 0):
            errs.append(f"{path}: 不能为空")
        if "maxLength" in schema and len(v) > schema["maxLength"]:
            errs.append(f"{path}: 超过 {schema['maxLength']} 字")
        if "pattern" in schema and not re.search(schema["pattern"], v):
            errs.append(f"{path}: 格式不对")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if "minimum" in schema and v < schema["minimum"]:
            errs.append(f"{path}: 不能小于 {schema['minimum']}")
        if "maximum" in schema and v > schema["maximum"]:
            errs.append(f"{path}: 不能大于 {schema['maximum']}")
    if isinstance(v, dict):
        for k in schema.get("required", []):
            if k not in v:
                errs.append(f"{path}: 缺少必填字段 {k}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, x in v.items():
            if k in props:
                errs += validate(x, props[k], f"{path}.{k}")
            elif extra is False:
                errs.append(f"{path}: 不认识的字段 {k}")
            elif isinstance(extra, dict):
                errs += validate(x, extra, f"{path}.{k}")
    if isinstance(v, list):
        if len(v) < schema.get("minItems", 0):
            errs.append(f"{path}: 至少 {schema['minItems']} 项")
        if "maxItems" in schema and len(v) > schema["maxItems"]:
            errs.append(f"{path}: 最多 {schema['maxItems']} 项")
        if "items" in schema:
            for i, x in enumerate(v):
                errs += validate(x, schema["items"], f"{path}[{i}]")
    if "oneOf" in schema:
        ok = sum(1 for sub in schema["oneOf"] if not validate(v, sub, path))
        if ok != 1:
            errs.append(f"{path}: 必须且只能满足一种写法（oneOf，当前满足 {ok} 种）")
    if "anyOf" in schema and not any(not validate(v, sub, path) for sub in schema["anyOf"]):
        errs.append(f"{path}: 不满足任何一种写法（anyOf）")
    return errs
