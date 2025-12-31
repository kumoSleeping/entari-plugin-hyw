# Entari Plugin HYW - LLM 指导手册

此文档旨在帮助 LLM 理解如何配置和使用 **Entari Plugin HYW**。

## 1. 交互逻辑与使用方法

### 核心工作流
- **触发**: 用户使用指令前缀（默认为 `/q`）。
- **上下文**: 支持通过回复消息进行的连续对话（多轮对话）。
- **多模态**: 支持图片分析。用户可以随指令发送图片，或通过回复图片来提问。
- **能力**: 包含网络搜索 (DuckDuckGo)、网页深度抓取 (Crawl4AI) 和逻辑推理。
- **输出**: 回答将渲染为包含 Markdown、代码高亮、LaTeX 公式和引用角标的高质量图片。

### 常用命令
- `/q [问题]` - 标准文本提问。
- `/q [图片] [问题]` - 针对图片内容提问。
- `[quote: 用户消息] /q` - 通过回复消息对其他的对话提问, hyw 会自己抓取关键词。

---

## 2. 详细配置说明 (entari.yml)

配置通常位于 `entari.yml` 的 `plugins.entari_plugin_hyw` 路径下。

### 2.1 核心基础配置 (Basic)
这些配置项决定了插件的基本交互行为。

| 配置项 | 类型 | 默认值 | 详细说明 | 必须 |
| :--- | :--- | :--- | :--- | :---: |
| `question_command` | `string` | `/q` | 触发机器人的指令前缀。例如设置为 `.ask` 则需输入 `.ask 你的问题`。 | 否 |
| `reaction` | `boolean` | `false` | 收到指令后是否立刻在 IM 平台回复一个 ✨ 表情。当前深度支持 OneBot 11 (Lagrange)。 | 否 |
| `quote` | `boolean` | `true` | 回复时是否引用（回复）用户的原始消息。 | 否 |
| `save_conversation` | `boolean` | `false` | 是否将对话历史持久化到本地 JSON 文件（用于调试）。 | 否 |
| `render_timeout_ms` | `integer` | `6000` | Markdown 渲染图片的超时时间（毫秒）。如果网络慢或内容极长，可调高。 | 否 |
| `fusion_mode` | `bolean` | `false` | (实验性) 融合模式开关。 | 否 |
| `max_turns` | `integer` | `10` | 单次 Agent 执行的最大轮数限制。 | 否 |
| `language` | `string` | `Simplified Chinese` | 机器人的回复语言 (限制 128 字符)。可以设置为 "Auto" 让模型自动判断，或指定如 "English"。 | 否 |

### 2.2 模型配置矩阵 (Model Matrix)
插件采用三阶段架构（Vision -> Instruct -> Agent）。
**主代理 (Main Agent)** 用于最终的逻辑推理和生成。其他阶段若未明确配置，将自动回退（Fallback）至此配置。

| 配置项 | 类型 | 详细说明 | 必须 |
| :--- | :--- | :--- | :---: |
| `model_name` | `string` | **核心 ID**。例如 `gpt-4o`, `google/gemini-2.0-flash-exp`。 | **是** |
| `api_key` | `string` | API 密钥。 | **是** |
| `base_url` | `string` | API 根地址。默认 `https://openrouter.ai/api/v1`。 | 否 |
| `extra_body` | `object` | 传递给 LLM RPC 的额外参数字典（如 `{"reasoning_effort": "high"}`）。 | 否 |
| `temperature` | `float` | 采样温度 (默认 0.4)。控制生成结果的确定性。 | 否 |
| `icon` | `string` | 底部提供商图标。支持列表：`anthropic`, `deepseek`, `gemini`, `google`, `grok`, `huggingface`, `microsoft`, `minimax`, `mistral`, `nvida`, `openai`, `openrouter`, `perplexity`, `qwen`, `xai`, `xiaomi`, `zai`。 | 否 |

**视觉分析 (Vision) 与 意图识别 (Instruct) 独立配置**
当需要为视觉处理或意图识别指定特定模型时使用。
- **如果不填**: 自动使用主 `model_name`, `api_key`, `base_url`。

| 配置项 | 类型 | 详细说明 |
| :--- | :--- | :--- |
| `vision_model_name` | `string` | 指定视觉模型。若主模型不支持图片，请务必填写此项。 |
| `vision_api_key` | `string` | 视觉模型的独立密钥。 |
| `vision_base_url` | `string` | 视觉模型的独立地址。 |
| `vision_extra_body` | `object` | 视觉模型的额外参数。 |
| `vision_icon` | `string` | 视觉模型独立图标。 |
| `instruct_model_name` | `string` | 意图识别模型。推荐使用高速模型（如 Gemini Flash）。 |
| `instruct_api_key` | `string` | 意图识别独立密钥。 |
| `instruct_base_url` | `string` | 意图识别独立地址。 |
| `instruct_extra_body` | `object` | 意图模型的额外参数。 |
| `instruct_icon` | `string` | 意图模型独立图标。 |

### 2.3 工具与搜索配置 (Tools)
| 配置项 | 类型 | 默认值 | 详细说明 |
| :--- | :--- | :--- | :--- |
| `search_provider` | `string` | `crawl4ai` | 搜索后端。`ddgs` (仅 DuckDuckGo), `crawl4ai` (推荐 DuckDuckGo), `httpx` (推荐 Jina AI/SearXNG HTML)。 |
| `search_limit` | `integer` | `8` | 每次搜索返回的最大结果条数。 |
| `headless` | `boolean` | `true` | 是否以无头模式运行浏览器（Crawl4AI 需要）。 |
| `search_base_url` | `string` | *DuckDuckGo Lite* | 文本搜索 URL (含 `{query}`)。`httpx` 模式建议使用 Jina AI (Get Search) 或 SearXNG。 |
| `image_search_base_url` | `string` | *DuckDuckGo* | 自定义图片搜索的基础 URL (含 `{query}` 占位符)。 |
| `search_params` | `string` | *None* | 搜索的附加参数 (如 `&kl=cn-zh` 指定中国地区)。 |
| `enable_browser_fallback` | `boolean` | `false` | 当主 LLM 生成失败时，是否尝试直接抓取 (由代码逻辑决定是否生效)。 |

### 2.4 计费与统计配置 (Billing)
用于在 UI 底部显示本次请求的估算成本。单位通常为 **美元/每百万 Token**。

| 配置项 | 类型 | 说明 |
| :--- | :--- | :--- |
| `input_price` | `float` | 主模型每 1M 输入 Token 价格。 |
| `output_price` | `float` | 主模型每 1M 输出 Token 价格。 |
| `vision_input_price` | `float` | 视觉模型输入单价（不填则跟随主模型）。 |
| `vision_output_price` | `float` | 视觉模型输出单价（不填则跟随主模型）。 |
| `instruct_input_price`| `float` | 意图模型输入单价（不填则跟随主模型）。 |
| `instruct_output_price`| `float` | 意图模型输出单价（不填则跟随主模型）。 |

---

## 3. 故障排查与指导建议 (Troubleshooting)

1.  **鉴权与连接**:
    *   **鉴权失败**: 检查 `api_key` 是否正确。
    *   **连接失败**: 检查 `base_url` 是否为 API 的根地址（通常应包含 `/v1` 后缀）。
2.  **多模态问题**:
    *   如果主模型没有 Vision 能力导致识图失败，请指导用户配置 `vision_model_name` 使用专门的视觉模型。
    *   如果搜索结果不理想，建议尝试更换 `search_provider`。

---

## 4. 实际配置案例 (Real World Example)

以下是一个基于真实生产环境的完整配置示例。

```yaml
plugins:
  entari_plugin_hyw:
    # --- 基础行为 ---
    question_command: "/ai"      # 自定义触发指令
    headless: true              # 无头模式运行浏览器
    save_conversation: true     # 保存对话记录以供调试
    reaction: false             # 关闭表情回应
    quote: false                # 关闭引用回复
    
    # --- 搜索配置 (使用自建/第三方聚合接口) ---
    search_provider: "ddgs"
    search_limit: 12

    # --- 主代理 (Main Agent) ---
    # 使用 Cerebras 只有文本模型，速度极快
    model_name: "qwen-3-235b-a22b-instruct-2507"
    api_key: "csk-..."
    base_url: "https://api.cerebras.ai/v1"
    icon: "qwen"

    # --- 视觉 (Vision) ---
    # 由于主模型不支持视觉，单独配置 Gemini Flash 处理图片
    vision_model_name: google/gemini-3-flash-preview
    vision_api_key: "sk-or-v1-..."
    vision_icon: "gemini"

    # --- 意图 (Instruct) ---
    # 未配置，自动回退使用 Main Agent
```
