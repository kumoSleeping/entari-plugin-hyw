# Entari Plugin HYW

[![PyPI version](https://badge.fury.io/py/entari-plugin-hyw.svg)](https://badge.fury.io/py/entari-plugin-hyw)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Versions](https://img.shields.io/pypi/pyversions/entari-plugin-hyw.svg)](https://pypi.org/project/entari-plugin-hyw/)

[English](../README.md) | **简体中文**

**Entari Plugin HYW** 是 Entari 框架的高级智能体聊天插件。它利用大语言模型（LLM）在即时通讯环境（OneBot 11, Satori）中提供智能、上下文感知和多模态的回复体验。

插件实现了三阶段流水线（**视觉**、**指令**、**代理**），能够自主决定何时搜索网络、抓取网页或分析图片，从而高效地回答用户问题。

<p align="center">
  <img src="demo_mockup.svg" width="800" />
</p>

## 功能特性

- 📖 **智能工作流 (Agentic Workflow)**  
  具备自主决策能力，能够自动进行搜索、网页浏览和逻辑推理。

- 🎑 **多模态支持 (Multi-Modal Support)**  
  原生支持图片分析，利用视觉语言模型（VLM）理解图像内容。

- 🔍 **搜索与抓取 (Web Search & Crawling)**  
  集成 DuckDuckGo 搜索与 Crawl4AI 网页抓取，实时获取互联网信息。

- 🎨 **富媒体渲染 (Rich Rendering)**  
  回答将渲染为包含 Markdown、代码高亮、LaTeX 公式及引用角标的精美图片。

- 🔌 **多协议适配 (Protocol Support)**  
  深度适配 OneBot 11 和 Satori 协议，完美处理回复上下文与 JSON 卡片。

## 安装

```bash
pip install entari-plugin-hyw
```

## 配置

在 `entari.yml` 中进行配置。

### 最小配置

```yaml
plugins:
  entari_plugin_hyw:
    model_name: google/gemini-2.0-flash-exp
    api_key: "your-or-api-key-here"
    # 渲染配置
    render_timeout_ms: 6000 # 浏览器等待超时
    render_image_timeout_ms: 3000 # 图片加载等待超时
```

## 使用方法

### 指令

- **文本问答**
  ```text
  /q Rust 1.83 有什么新特性？
  ```

- **图片分析**
  *(发送带图片的指令，或回复一张图片)*
  ```text
  /q [图片] 解释一下这个报错。
  ```

- **引用提问**
  ```text
  [quote: 用户消息] /q
  ```

- **追问**
  *直接回复机器人的消息即可进行连续对话。*

## AI/LLM 文档

- [指导手册 (简体中文)](README_LLM_CN.md)
- [Instruction Guide (English)](README_LLM_EN.md)

-----

## 许可证

本项目采用 MIT 许可证。
