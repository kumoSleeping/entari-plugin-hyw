<div align="center">

# Entari Plugin HYW

**Entari 智能聊天解释插件**

[![License](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](https://opensource.org/licenses/MIT) [![PyPI](https://img.shields.io/pypi/v/entari-plugin-hyw?style=flat-square&color=success)](https://pypi.org/project/entari-plugin-hyw/) [![Python](https://img.shields.io/badge/Python-3.10+-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)

*IM 环境下的 LLM 智能解释方案*

</div>

## 🎑 效果展示



<div align="center">
  <img src="demo.svg" alt="Chat Demo" width="800" style="display: block; margin: 0;">
</div>

## ✨ 功能特性
- **关于搜索**：目前推荐使用 OpenRouter 的 `:online` 参数，该参数会优先使用模型提供商的搜索、其次 `exa`(较贵) 进行网页搜索。推荐使用 `x-ai/grok-4.1-fast:online`。
- 给予 `Alconna` 与 `MessageChain` 混合处理, 深度优化触发体验`。
- **网页获取**：支持通过 **Jina AI** 或 **Playwright** 进行实时页面获取。
- **多模态理解**：无缝处理文本与图片。自动对文档/截图进行 OCR 文字识别，对照片进行视觉分析。
- **上下文感知**：维护对话历史记录，支持自然、连续的多轮对话。支持保存对话历史记录搭到本地研究。
- **OneBot 优化**：针对 OneBot 11 协议深度优化，支持解析 JSON 卡片、引用消息等特殊元素。
- `reaction` 表情, 表示任务开始。



## 📦 安装

### 基础安装
```bash
pip install entari-plugin-hyw
```

### 启用 Playwright 支持
如果你希望使用 Playwright 进行本地网页渲染（而非仅使用 Jina AI）：
```bash
pip install entari-plugin-hyw[playwright]
playwright install chromium
```

## ⚙️ 配置

请在 `entari.yml` 中添加以下配置：

```yaml
plugins:
  entari_plugin_hyw:
    # --- 基础设置 ---
    # 触发机器人的命令列表
    command_name_list: ["/hyw", "hyw"]
    
    # 主 LLM 模型配置（必需）(online 模式), 如 x-ai/grok-4.1-fast:online、perplexity/sonar
    model_name: "gx-ai/grok-4.1-fast:online"
    api_key: "your-api-key"
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    
    # --- 浏览器与搜索 ---
    # 网页浏览工具: "jina" (默认) 或 "playwright"
    browser_tool: "jina"
    
    # 可选: Jina AI API Key (配置以获得更高限额)(免费方案20/min)
    jina_api_key: "jina_..."
    
    # Playwright 设置
    headless: true
    
    # --- 视觉配置 (可选) ---
    # 如果未设置，将回退使用主模型
    vision_model_name: "qwen-vl-plus"
    vision_api_key: "your-vision-api-key"
    vision_base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    
    # --- 调试 ---
    save_conversation: false
```

## 📖 使用方法

### 基础指令
使用配置的命令前缀与机器人交互：

```text
hyw 最近LLM有啥新闻, 是不是claude又被秒了
hyw [图片消息] 里面这人写代码怎么我一句都看不懂
hyw https://koishi.chat/ 怎么安装
[回复消息] hyw 
[回复消息<[图片消息]>] hyw -t
[回复消息] hyw 补充: 这个rf的意思是github用户RF-Tar-Railt
[回复消息(hyw插件的输出)] /1 详细点描述
[回复消息(hyw插件的输出>] /那谁有多余解释器?
```

### 选项参数
- `-t` / `--text`: 强制纯文本模式（跳过图片分析，节省 Token 或时间）。

```text
hyw -t 一大段话。
```

### 引用回复
支持引用消息进行追问，机器人会自动读取被引用的消息作为上下文：
- **引用 + 命令**：机器人将理解被引用消息的内容（包括图片）通过 `MessageChain` 操作拼接 `Text`、`Image` 与部分 `Custom`。
