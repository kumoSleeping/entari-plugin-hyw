# Entari Plugin HYW

[![PyPI version](https://badge.fury.io/py/entari-plugin-hyw.svg)](https://badge.fury.io/py/entari-plugin-hyw)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Versions](https://img.shields.io/pypi/pyversions/entari-plugin-hyw.svg)](https://pypi.org/project/entari-plugin-hyw/)

**English** | [简体中文](docs/README_CN.md)

**Entari Plugin HYW** is an advanced agentic chat plugin for the [Entari](https://github.com/entari-org/entari) framework. It leverages Large Language Models (LLMs) to provide intelligent, context-aware, and multi-modal responses within instant messaging environments (OneBot 11, Satori).

The plugin implements a three-stage pipeline (**Vision**, **Instruct**, **Agent**) to autonomously decide when to search the web, crawl pages, or analyze images to answer user queries effectively.

<p align="center">
  <img src="docs/demo_mockup.svg" width="800" />
</p>

## Features

- 📖 **Agentic Workflow**  
  Autonomous decision-making process to search, browse, and reason.

- 🎑 **Multi-Modal Support**  
  Native support for image analysis using Vision Language Models (VLMs).

- 🔍 **Web Search & Crawling**  
  Integrated **DuckDuckGo** and **Crawl4AI** for real-time information retrieval.

- 🎨 **Rich Rendering**  
  Responses are rendered as images containing Markdown, syntax-highlighted code, LaTeX math, and citation badges.

- 🔌 **Protocol Support**  
  Deep integration with OneBot 11 and Satori protocols, handling reply context and JSON cards perfectly.

## Installation

```bash
pip install entari-plugin-hyw
```

## Configuration

Configure the plugin in your `entari.yml`.

### Minimal Configuration

```yaml
plugins:
  entari_plugin_hyw:
    model_name: google/gemini-2.0-flash-exp
    api_key: "your-or-api-key-here"
    # Rendering Configuration
    render_timeout_ms: 6000 # Browser wait timeout
    render_image_timeout_ms: 3000 # Image load wait timeout
```

## Usage

### Commands

- **Text Query**
  ```text
  /q What's the latest news on Rust 1.83?
  ```

- **Image Analysis**
  *(Send an image with command, or reply to an image)*
  ```text
  /q [Image] Explain this error.
  ```
- **Quote Query**
  ```text
  [quote: User Message] /q
  ```

- **Follow-up**
  *Reply to the bot's message to continue the conversation.*

## Documentation for AI/LLMs

- [Instruction Guide (English)](docs/README_LLM_EN.md)
- [指导手册 (简体中文)](docs/README_LLM_CN.md)

---

## License

This project is licensed under the MIT License.