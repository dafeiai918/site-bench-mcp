# site-bench-mcp · 中转站质量评测 MCP

**让 AI 直接回答：「这个站快不快？」「是不是降智了？」「哪个站更靠谱？」**

零依赖 · 单文件 · stdio MCP 服务器 · 中文

---

## 它让 AI 能做什么

> 「测一下这个站 https://api.example.com/v1 快不快，密钥 sk-xxx」
> → AI 调用 `check_latency`，返回首 Token 延迟与总耗时

> 「帮我对这个站做完整体检」
> → AI 调用 `bench_site`，返回延迟 / 并发 / 参数兼容 / 能力抽样四段报告

> 「这个站是不是把模型换成小的了？」
> → AI 调用 `check_degradation`，用 4 道陷阱题抽样判断

> 「我有两个站，哪个更快？」
> → AI 调用 `compare_sites` 横向对比

---

## 提供的工具

| 工具 | 作用 | 消耗 |
|---|---|---|
| `check_latency` | 首 Token 延迟 + 总耗时 | 1 次调用 |
| `bench_site` | 完整四项体检 | 约 10 次调用 |
| `check_degradation` | 4 道陷阱题抽样判断降智 | 4 次调用 |
| `compare_sites` | 多站横向对比延迟 | 每站 1 次 |

---

## 安装

### 方式一：uvx 一行（推荐）

客户端配置（Claude Desktop / Cherry Studio / Cursor / Cline）：

```json
{
  "mcpServers": {
    "site-bench-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/dafeiai918/site-bench-mcp", "site-bench-mcp"]
    }
  }
}
```

### 方式二：本地文件

```json
{
  "mcpServers": {
    "site-bench-mcp": {
      "command": "python",
      "args": ["/绝对路径/site-bench-mcp/site_bench_mcp.py"]
    }
  }
}
```

零第三方依赖，Python 3.9+ 即可。

---

## 体检报告长什么样

```
站点体检报告
地址: https://api.example.com/v1
模型: deepseek-chat

【延迟】
- first_token_s: 0.62
- total_s: 1.84

【并发(3)】
- success_rate: 1.0
- avg_s: 2.11

【参数兼容】
- max_tokens: True
- json_mode: True
- stop: True

【能力抽样】
  ✓ 9.11 与 9.9 哪… → 9.9
  ✓ 单词 strawberr… → 3
  ✓ 如果今天是星期… → 星期六
  ✓ 篮子里有5个苹果… → 6

说明：能力抽样是启发式信号，不是模型真伪鉴定，仅供参考。
```

---

## 评测项说明

| 项目 | 怎么测 | 怎么看 |
|---|---|---|
| **延迟** | 流式请求，量首 Token 时间 | <1s 很好，<3s 可接受，>5s 偏慢 |
| **并发** | N 个请求同时发 | 成功率 100% 才算稳 |
| **参数兼容** | `max_tokens` / `json_mode` / `stop` | 不兼容说明网关实现粗糙 |
| **能力抽样** | 4 道常见陷阱题 | 通过 < 3 题需警惕 |

**能力抽样用的题**：数字比较（9.11 vs 9.9）、字母计数（strawberry）、日期推算、简单运算——都是小模型容易答错、大模型基本不错的题。

> ⚠️ **这不是模型真伪鉴定**。样本量小，可能误判。要下结论请多测几轮，或配合 [model-arena](https://github.com/dafeiai918/model-arena) 做盲测。

---

## 消耗提示

- `bench_site` 会产生约 10 次 API 调用（你自己站的额度）
- 并发测试建议用 `concurrency=3`，别开太大
- 全部工具都是标准 OpenAI 接口调用，不涉及任何破解

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [relay-bench](https://github.com/dafeiai918/relay-bench) | 同款能力的命令行版（报告更详细） |
| [relay-mcp](https://github.com/dafeiai918/relay-mcp) | 中转站账户管理 MCP（余额 / 模型列表） |
| [model-arena](https://github.com/dafeiai918/model-arena) | 模型盲测 Elo 排行榜 |
| [awesome-relay](https://github.com/dafeiai918/awesome-relay) | 中转站与客户端精选导航 |

---

## License

MIT
