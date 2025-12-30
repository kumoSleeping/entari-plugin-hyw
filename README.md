
# Entari Plugin HYW


[![PyPI version](https://badge.fury.io/py/entari-plugin-hyw.svg)](https://badge.fury.io/py/entari-plugin-hyw)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Versions](https://img.shields.io/pypi/pyversions/entari-plugin-hyw.svg)](https://pypi.org/project/entari-plugin-hyw/)

**Entari Plugin HYW** is an advanced agentic chat plugin for the [Entari](https://github.com/entari-org/entari) framework. It leverages Large Language Models (LLMs) to provide intelligent, context-aware, and multi-modal responses within instant messaging environments (OneBot 11, Satori).

**Entari Plugin HYW** 是 Entari 框架的高级智能体聊天插件。它利用大语言模型（LLM）在即时通讯环境（OneBot 11, Satori）中提供智能、上下文感知和多模态的回复体验。

The plugin implements a three-stage pipeline (**Vision**, **Instruct**, **Agent**) to autonomously decide when to search the web, crawl pages, or analyze images to answer user queries effectively.

插件实现了三阶段流水线（**视觉**、**指令**、**代理**），能够自主决定何时搜索网络、抓取网页或分析图片，从而高效地回答用户问题。

<img src="demo.jpg" width="300" />

## Features / 功能特性

- 📖 **Agentic Workflow (智能工作流)**  
  Autonomous decision-making process to search, browse, and reason.  
  具备自主决策能力，能够自动进行搜索、网页浏览和逻辑推理。

- 🎑 **Multi-Modal Support (多模态支持)**  
  Native support for image analysis using Vision Language Models (VLMs).  
  原生支持图片分析，利用视觉语言模型（VLM）理解图像内容。

- 🔍 **Web Search & Crawling (搜索与抓取)**  
  Integrated **DuckDuckGo** and **Crawl4AI** for real-time information retrieval.  
  集成 DuckDuckGo 搜索与 Crawl4AI 网页抓取，实时获取互联网信息。

- 🎨 **Rich Rendering (富媒体渲染)**  
  Responses are rendered as images containing Markdown, syntax-highlighted code, LaTeX math, and citation badges.  
  回答将渲染为包含 Markdown、代码高亮、LaTeX 公式及引用角标的精美图片。

- 🔌 **Protocol Support (多协议适配)**  
  Deep integration with OneBot 11 and Satori protocols.  
  深度适配 OneBot 11 和 Satori 协议，完美处理回复上下文与 JSON 卡片。

## Installation / 安装

```bash
pip install entari-plugin-hyw
```

## Configuration / 配置

Configure the plugin in your `entari.yml`.  
在 `entari.yml` 中进行配置。

### Minimal Configuration / 最小配置

```yaml
plugins:
  entari_plugin_hyw:
    # Trigger command / 触发指令
    question_command: ".q"
    
    # Main Model (Required) / 主模型（必需）
    model_name: "google/gemini-2.0-flash-exp"
    api_key: "your-api-key-here"
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
```

### Configuration Reference / 配置详解

| Option (选项) | Type | Default | Description (说明) |
| :--- | :--- | :--- | :--- |
| **Basic** | | | |
| `question_command` | `str` | `/q` | The command to trigger the bot. <br> 触发机器人的指令前缀。 |
| `reaction` | `bool` | `true` | React with emoji on start(now only lagrange ob extension). <br> 收到指令时是否回应表情(目前只支持拉格兰ob扩展)。 |
| `quote` | `bool` | `true` | Quote the user's message in reply. <br> 回复时是否引用原消息。 |
| **Models** | | | |
| `model_name` | `str` | *None* | **Required.** Main Agent model ID. <br> **必需。** 主代理模型 ID。 |
| `api_key` | `str` | *None* | **Required.** API key. <br> **必需。** API 密钥。 |
| `base_url` | `str` | `...` | OpenAI-compatible API base URL. <br> 兼容 OpenAI 的 API 地址。 |
| `extra_body` | `dict` | `null` | Extra parameters (e.g. `reasoning_effort`). <br> 传递给 LLM 的额外参数。 |
| **Specialized** | | | |
| `vision_model_name`| `str` | *None* | Model for images. Defaults to `model_name`. <br> 处理图片的模型，默认同主模型。 |
| `intruct_model_name`| `str` | *None* | Model for intent. Defaults to `model_name`. <br> 意图识别模型，默认同主模型。 |
| **Tools** | | | |
| `search_provider` | `str` | `ddgs`| `ddgs` (DuckDuckGo), `crawl4ai`, `httpx`. <br> 搜索后端提供商。 |
| `search_limit` | `int` | `8` | Max search results. <br> 搜索结果数量限制。 |
| `headless` | `bool` | `true` | Browser headless mode. <br> 浏览器无头模式。 |

## Usage / 使用方法

### Commands / 指令

- **Text Query (文本问答)**
  ```text
  .q What's the latest news on Rust 1.83?
  .q Rust 1.83 有什么新特性？
  ```

- **Image Analysis (图片分析)**
  *(Send an image with command, or reply to an image)*  
  *(发送带图片的指令，或回复一张图片)*
  ```text
  .q [Image] Explain this error.
  .q [图片] 解释一下这个报错。
  ```

- **Follow-up (追问)**
  *Reply to the bot's message to continue the conversation.*  
  *直接回复机器人的消息即可进行连续对话。*

-----

## License

This project is licensed under the MIT License.