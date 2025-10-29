# entari-plugin-hyw

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![PyPI version](https://img.shields.io/badge/PyPI-v0.3.5-brightgreen.svg)](https://pypi.org/project/entari-plugin-hyw/)
[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Entari](https://img.shields.io/badge/Entari-0.16.5+-orange.svg)](https://github.com/ArcletProject/entari)

一个基于大语言模型的智能聊天解释插件，专为 Entari 机器人框架设计，支持多模态内容理解和智能搜索。

## ✨ 核心特性

- **多模态解析**：
  - 同时处理文本[网页链接][小程序]、图片内容
  - 支持回复消息的上下文理解, 即使包含At元素也能正确处理
  - 解析 QQ 小程序分享内容

- **专注搜索与百科**:
  - 支持多个搜索引擎同时搜索, 支持 ::exact 后缀进行精确搜索
  - 过滤广告、噪音信息
  - 使用 Jina AI 获取和便宜模型压缩网页内容
- **最低成本**：依托爬虫搜索引擎库与免费的jina AI，降低使用成本
- **表情反应**：自动添加处理状态的表情反应



> 目前针对 onebot11 适配器进行了消息元素优化


## 🚀 快速开始

### 安装

```bash
pip install entari-plugin-hyw
```
### 配置

在你的 `entari.yml` 配置文件中添加以下配置：

> 大部分搜索引擎, 如 DuckDuckGo 在中国大陆地区请搭配代理使用.

```yaml
plugins:
  entari_plugin_hyw:
    # 命令配置
    hyw_command_name: ["/hyw", "hyw"]
    
    # 文本模型配置（必需）
    text_llm_model_name: "qwen3-max"
    text_llm_api_key: "your-text-api-key"
    text_llm_model_base_url: "https://your-llm-api.com/v1"
    text_llm_temperature: 0.4
    text_llm_enable_search: false
    
    # 视觉模型配置（必需）
    vision_llm_model_name: "qwen3-vl-plus"
    vision_llm_api_key: "your-vision-api-key"
    vision_llm_model_base_url: "https://your-llm-api.com/v1"
    vision_llm_temperature: 0.4
    vision_llm_enable_search: false
    
    # 压缩器模型配置（使用便宜模型）
    compressor_llm_model_name: "qwen-flash"
    compressor_llm_api_key: "your-compressor-api-key"
    compressor_llm_model_base_url: "https://your-llm-api.com/v1"
    compressor_llm_temperature: 0.1
    
    # 搜索引擎配置
    search_engines: ["duckduckgo", "duckduckgo::exact"]
```

## 📖 使用方法

### 基础用法

```
hyw 什么是人工智能？
hyw [图片]
hyw https://example.com
```

### 引用回复

```
[引用消息[图片, 文字]] hyw
[引用消息[图片, 文字]] [At] hyw 什么是人工智能？ [图片]
```

## ⚙️ 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `hyw_command_name` | `str \| List[str]` | `"hyw"` | 触发命令名称 |
| `text_llm_model_name` | `str` | - | 文本模型名称（必需） |
| `text_llm_api_key` | `str` | - | 文本模型 API 密钥（必需） |
| `text_llm_model_base_url` | `str` | - | 文本模型 API 地址（必需） |
| `text_llm_temperature` | `float` | `0.4` | 文本模型温度参数 |
| `text_llm_enable_search` | `bool` | `false` | 是否启用模型内搜索 |
| `vision_llm_model_name` | `str` | - | 视觉模型名称（必需） |
| `vision_llm_api_key` | `str` | - | 视觉模型 API 密钥（必需） |
| `vision_llm_model_base_url` | `str` | - | 视觉模型 API 地址（必需） |
| `vision_llm_temperature` | `float` | `0.4` | 视觉模型温度参数 |
| `vision_llm_enable_search` | `bool` | `false` | 是否启用视觉搜索 |
| `compressor_llm_model_name` | `str` | - | 压缩器模型名称 |
| `compressor_llm_api_key` | `str` | - | 压缩器模型 API 密钥 |
| `compressor_llm_model_base_url` | `str` | - | 压缩器模型 API 地址 |
| `compressor_llm_temperature` | `float` | `0.1` | 压缩器模型温度 |
| `search_engines` | `List[str]` | `["auto"]` | 搜索引擎列表 |



## 模型选择建议

- **文本模型**：推荐使用 Qwen-Max 等高性能模型
- **视觉模型**：推荐使用 Qwen-VL-Plus
- **压缩器模型**：推荐使用 Qwen-Flash、DeepSeek-Chat 等经济型模型

