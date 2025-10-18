# 🤖 entari-plugin-hyw


![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)![License](https://img.shields.io/badge/License-MIT-green.svg)![Version](https://img.shields.io/badge/Version-0.1.1-orange.svg)![Platform](https://img.shields.io/badge/Platform-Entari-purple.svg)![PyPI]()

**使用大语言模型在聊天环境解释大家的hyw**

</div>


## 🚀 快速开始

### 安装

```bash
pip install entari-plugin-hyw
```

### 支持适配器

> 目前仅支持 satori-python-adapter-onebot11 使用此插件, 更多适配请等一会...

### 基础配置

在你的 `entari.yml` 配置文件中根据您的情况添加以下配置：

```yaml
plugins:
  src.entari_plugin_hyw:
    hyw_command_name: ["/hyw", "hyw"]
    
    # 文本模型配置
    text_llm_model_name: "qwen3-max"
    text_llm_api_key: "your-api-key"
    text_llm_model_base_url: "https://xxx/v1"
    text_llm_enable_search: false
    
    # 视觉模型配置
    vision_llm_model_name: "qwen3-vl-plus"
    vision_llm_api_key: "your-api-key"
    vision_llm_model_base_url: "https://xxx/v1"
    vision_llm_enable_search: false
```

## 📖 使用方法

文本问答

```
hyw 什么是人工智能？
```


发送图片并使用命令：

```
hyw [图片]
```

引用一条消息然后使用命令：

```
hyw [引用消息[图片, 文字]]
```

> 触发将会自动屏蔽 At 元素

## ⚙️ 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `hyw_command_name` | `str \| List[str]` | `"hyw"` | 触发命令名称 |
| `text_llm_model_name` | `str` | - | 文本模型名称 |
| `text_llm_api_key` | `str` | - | 文本模型 API 密钥 |
| `text_llm_model_base_url` | `str` | - | 文本模型 API 地址 |
| `text_llm_temperature` | `float` | `0.4` | 文本模型温度参数 |
| `text_llm_enable_search` | `bool` | `false` | 是否启用搜索功能 |
| `vision_llm_model_name` | `str` | - | 视觉模型名称 |
| `vision_llm_api_key` | `str` | - | 视觉模型 API 密钥 |
| `vision_llm_model_base_url` | `str` | - | 视觉模型 API 地址 |
| `vision_llm_temperature` | `float` | `0.4` | 视觉模型温度参数 |
| `vision_llm_enable_search` | `bool` | `false` | 是否启用视觉搜索 |
| `hyw_prompt` | `str` | 默认提示词 | 自定义系统提示词 |





