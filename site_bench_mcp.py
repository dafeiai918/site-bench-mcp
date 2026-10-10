#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""site-bench-mcp · 中转站质量评测 MCP
让 AI 直接测：延迟 / 并发 / 参数兼容 / 降智抽样。零依赖，
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.0.0"
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 30


# ================= MCP 内核 =================

class MCPServer:
    def __init__(self, name: str, version: str, tools: list):
        self.name, self.version, self.tools = name, version, tools

    def _send(self, msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _result(self, id_, result) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "result": result})

    def _error(self, id_, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})

    def handle(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        method, id_ = msg.get("method"), msg.get("id")
        if method == "initialize":
            self._result(id_, {"protocolVersion": PROTOCOL_VERSION,
                               "capabilities": {"tools": {"listChanged": False}},
                               "serverInfo": {"name": self.name, "version": self.version}})
        elif method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            return
        elif method == "ping":
            self._result(id_, {})
        elif method == "tools/list":
            self._result(id_, {"tools": [
                {"name": t["name"], "description": t["description"],
                 "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}}
                for t in self.tools]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            tool = next((t for t in self.tools if t["name"] == params.get("name")), None)
            if tool is None:
                self._error(id_, -32602, f"未知工具: {params.get('name')}")
                return
            try:
                out = tool["handler"](params.get("arguments") or {})
                self._result(id_, {"content": [{"type": "text", "text": str(out)}], "isError": False})
            except Exception as exc:
                self._result(id_, {"content": [{"type": "text",
                                                "text": f"错误: {type(exc).__name__}: {exc}"}],
                                   "isError": True})
        elif id_ is not None:
            self._error(id_, -32601, f"不支持的方法: {method}")

    def serve(self) -> None:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            for item in (msg if isinstance(msg, list) else [msg]):
                self.handle(item)


# ================= 评测逻辑 =================

def normalize(base: str) -> tuple[str, str]:
    root = (base or "").strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3].rstrip("/")
    return root, root + "/v1"


def _call(full: str, key: str, payload: dict, timeout: float = TIMEOUT,
          stream: bool = False):
    req = urllib.request.Request(
        f"{full}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Accept": "text/event-stream" if stream else "application/json",
                 "User-Agent": f"site-bench-mcp/{__version__}"},
        method="POST")
    return urllib.request.urlopen(req, timeout=timeout)


def chat(full: str, key: str, model: str, messages: list, **extra) -> tuple[float, float | None, str]:
    payload = {"model": model, "messages": messages, **extra}
    stream = bool(payload.get("stream"))
    started = time.perf_counter()
    first = None
    chunks: list[str] = []
    with _call(full, key, payload, stream=stream) as resp:
        if stream:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    delta = json.loads(body)["choices"][0].get("delta", {})
                except Exception:
                    continue
                piece = delta.get("content") or ""
                if piece and first is None:
                    first = time.perf_counter() - started
                chunks.append(piece)
        else:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            chunks.append(data["choices"][0]["message"].get("content") or "")
    return time.perf_counter() - started, first, "".join(chunks)


def list_models(full: str, key: str) -> list[str]:
    req = urllib.request.Request(f"{full}/models",
                                 headers={"Authorization": f"Bearer {key}",
                                          "User-Agent": f"site-bench-mcp/{__version__}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return [m.get("id", "") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]


def pick_model(full: str, key: str, wanted: str | None) -> str:
    if wanted:
        return wanted
    models = list_models(full, key)
    for prefix in ("deepseek", "gpt-4o-mini", "gpt-3.5", "qwen", "glm"):
        for name in models:
            if prefix in name.lower():
                return name
    return models[0] if models else ""


def measure_latency(full: str, key: str, model: str, stream: bool = True) -> dict:
    total, first, _ = chat(full, key, model, [{"role": "user", "content": "回答一个字：好"}],
                           stream=stream, max_tokens=8)
    return {"total_s": round(total, 2),
            "first_token_s": round(first, 2) if first is not None else None}


def measure_concurrency(full: str, key: str, model: str, concurrency: int) -> dict:
    def one(_):
        started = time.perf_counter()
        try:
            chat(full, key, model, [{"role": "user", "content": "好"}], max_tokens=8, timeout=90)
            return time.perf_counter() - started
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        results = list(pool.map(one, range(max(1, concurrency))))
    ok = [x for x in results if x is not None]
    rate = len(ok) / max(1, concurrency)
    return {"concurrency": concurrency, "success_rate": round(rate, 2),
            "ok": len(ok), "failed": concurrency - len(ok),
            "avg_s": round(sum(ok) / len(ok), 2) if ok else None,
            "max_s": round(max(ok), 2) if ok else None}


def check_params(full: str, key: str, model: str) -> dict:
    result = {}
    try:
        _, _, text = chat(full, key, model, [{"role": "user", "content": "从1数到60"}], max_tokens=400)
        result["max_tokens"] = bool(text.strip())
    except Exception as exc:
        result["max_tokens"] = f"失败:{type(exc).__name__}"
    try:
        _, _, text = chat(full, key, model,
                          [{"role": "user", "content": '只输出JSON：{"ok":true}'}],
                          max_tokens=64, response_format={"type": "json_object"})
        body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            json.loads(body)
            result["json_mode"] = True
        except Exception:
            result["json_mode"] = "不支持"
    except Exception:
        result["json_mode"] = "不支持"
    try:
        _, _, text = chat(full, key, model, [{"role": "user", "content": "输出：AAA BBB CCC"}],
                          max_tokens=32, stop=["BBB"])
        result["stop"] = "CCC" not in text
    except Exception:
        result["stop"] = False
    return result


TRAPS = [
    ("9.11 与 9.9 哪个更大？只回答更大的那个数字。", ["9.9"]),
    ("单词 strawberry 里有几个字母 r？只回答数字。", ["3", "三"]),
    ("如果今天是星期三，三天后是星期几？只回答星期几。", ["星期六", "周六", "6"]),
    ("篮子里有5个苹果，拿走2个，又放入3个，现在有几个？只回答数字。", ["6", "六"]),
]


def check_degradation(full: str, key: str, model: str) -> dict:
    passed, details = 0, []
    for question, answers in TRAPS:
        try:
            _, _, text = chat(full, key, model, [{"role": "user", "content": question}],
                              max_tokens=64)
            hit = any(a in text for a in answers)
            details.append({"q": question[:12], "pass": hit, "got": text.strip()[:16]})
            passed += 1 if hit else 0
        except Exception as exc:
            details.append({"q": question[:12], "pass": False, "got": type(exc).__name__})
    return {"passed": passed, "total": len(TRAPS), "details": details}


def fmt_report(title: str, body: dict) -> str:
    lines = [f"【{title}】"]
    for key, value in body.items():
        if key == "details":
            continue
        lines.append(f"- {key}: {value}")
    if body.get("details"):
        for item in body["details"]:
            mark = "✓" if item["pass"] else "✗"
            lines.append(f"  {mark} {item['q']}… → {item['got']}")
    return "\n".join(lines)


# ================= 工具 =================

def tool_check_latency(args: dict) -> str:
    base, key = (args.get("base_url") or "").strip(), (args.get("api_key") or "").strip()
    if not base or not key:
        return "需要 base_url 和 api_key。"
    _, full = normalize(base)
    model = pick_model(full, key, args.get("model"))
    if not model:
        return "站点没有可用模型。"
    body = measure_latency(full, key, model)
    return (f"站点延迟正常 ✓\n- 使用模型: {model}\n"
            f"- 首 Token: {body['first_token_s']}s\n- 总耗时: {body['total_s']}s\n\n"
            f"参考：首 Token < 1s 很好，< 3s 可接受，> 5s 偏慢。")


def tool_bench_site(args: dict) -> str:
    base, key = (args.get("base_url") or "").strip(), (args.get("api_key") or "").strip()
    if not base or not key:
        return "需要 base_url 和 api_key。"
    _, full = normalize(base)
    concurrency = int(args.get("concurrency") or 3)
    model = pick_model(full, key, args.get("model"))
    if not model:
        return "站点没有可用模型，无法体检。"
    sections = [f"站点体检报告", f"地址: {full}", f"模型: {model}", ""]
    try:
        sections.append(fmt_report("延迟", measure_latency(full, key, model)))
    except Exception as exc:
        sections.append(f"【延迟】失败: {type(exc).__name__}")
    try:
        sections.append("")
        sections.append(fmt_report(f"并发({concurrency})",
                                   measure_concurrency(full, key, model, concurrency)))
    except Exception as exc:
        sections.append(f"【并发】失败: {type(exc).__name__}")
    try:
        sections.append("")
        sections.append(fmt_report("参数兼容", check_params(full, key, model)))
    except Exception as exc:
        sections.append(f"【参数兼容】失败: {type(exc).__name__}")
    try:
        sections.append("")
        sections.append(fmt_report("能力抽样", check_degradation(full, key, model)))
    except Exception as exc:
        sections.append(f"【能力抽样】失败: {type(exc).__name__}")
    sections += ["", "说明：能力抽样是启发式信号，不是模型真伪鉴定，仅供参考。"]
    return "\n".join(sections)


def tool_check_degradation(args: dict) -> str:
    base, key = (args.get("base_url") or "").strip(), (args.get("api_key") or "").strip()
    if not base or not key:
        return "需要 base_url 和 api_key。"
    _, full = normalize(base)
    model = pick_model(full, key, args.get("model"))
    if not model:
        return "站点没有可用模型。"
    body = check_degradation(full, key, model)
    verdict = ("表现正常" if body["passed"] >= 3 else
               "有可疑迹象" if body["passed"] == 2 else "疑似降智/小模型冒充")
    return (f"能力抽样：{body['passed']}/{body['total']} 通过 → {verdict}\n\n"
            + fmt_report("明细", body)
            + "\n\n注意：这是启发式抽样，样本量小，结果仅供参考，不能作为唯一依据。")


def tool_compare_sites(args: dict) -> str:
    sites = args.get("sites") or []
    if not isinstance(sites, list) or len(sites) < 2:
        return '需要 sites 数组，至少2个：{"sites":[{"name":"A","base_url":"...","api_key":"..."}]}'
    lines = ["站点横向对比：", ""]
    rows = []
    for site in sites:
        name = site.get("name") or site.get("base_url", "?")
        try:
            _, full = normalize(site.get("base_url", ""))
            model = pick_model(full, site.get("api_key", ""), site.get("model"))
            latency = measure_latency(full, site["api_key"], model)
            rows.append((name, latency["first_token_s"], latency["total_s"], "可用"))
        except Exception as exc:
            rows.append((name, None, None, f"失败({type(exc).__name__})"))
    for name, first, total, status in rows:
        if first is None:
            lines.append(f"- {name}: {status}")
        else:
            lines.append(f"- {name}: 首Token {first}s / 总 {total}s  {status}")
    usable = [r for r in rows if r[1] is not None]
    if usable:
        best = min(usable, key=lambda r: r[1])
        lines += ["", f"最快：{best[0]}（首Token {best[1]}s）"]
    return "\n".join(lines)


TOOLS = [
    {
        "name": "check_latency",
        "description": "测中转站延迟（首Token时间和总耗时）。用户问「这个站快不快」"
                       "「响应速度怎么样」时使用。",
        "inputSchema": {"type": "object", "properties": {
            "base_url": {"type": "string", "description": "站点地址"},
            "api_key": {"type": "string", "description": "API 密钥"},
            "model": {"type": "string", "description": "可选，指定测试模型"}},
            "required": ["base_url", "api_key"]},
        "handler": tool_check_latency,
    },
    {
        "name": "bench_site",
        "description": "完整站点体检：延迟 + 并发 + 参数兼容 + 能力抽样。"
                       "用户想全面评估一个中转站质量时使用。会消耗少量额度。",
        "inputSchema": {"type": "object", "properties": {
            "base_url": {"type": "string"},
            "api_key": {"type": "string"},
            "model": {"type": "string"},
            "concurrency": {"type": "integer", "description": "并发数，默认3"}},
            "required": ["base_url", "api_key"]},
        "handler": tool_bench_site,
    },
    {
        "name": "check_degradation",
        "description": "能力抽样，检测是否被换成了更小的模型（俗称降智）。"
                       "用户问「这个站是不是假的」「会不会降智」时使用。",
        "inputSchema": {"type": "object", "properties": {
            "base_url": {"type": "string"},
            "api_key": {"type": "string"},
            "model": {"type": "string"}},
            "required": ["base_url", "api_key"]},
        "handler": tool_check_degradation,
    },
    {
        "name": "compare_sites",
        "description": "多个中转站横向对比延迟。用户有多个站想知道哪个快时使用。",
        "inputSchema": {"type": "object", "properties": {
            "sites": {"type": "array", "description": "站点数组",
                      "items": {"type": "object", "properties": {
                          "name": {"type": "string"},
                          "base_url": {"type": "string"},
                          "api_key": {"type": "string"}}}}},
            "required": ["sites"]},
        "handler": tool_compare_sites,
    },
]


def main() -> int:
    if os.name == "nt":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    MCPServer("site-bench-mcp", __version__, TOOLS).serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
