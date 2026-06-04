# entari-plugin-hyw

HYW v6 是单一 Entari 插件包。仓库根目录就是插件包本体，不再包含 `src/`、Codex 插件、桌面端或 `core/` 包装层。

## 配置

```yaml
basic:
  external_dirs:
    - /path/to/parent

plugins:
  entari_plugin_hyw:
    question_command: ".q"
    api_key: "..."
    base_url: "https://llm.hyw.mom/v1"
    model_name: "gemini-3.5-flash"
    jina_key: "..."
```
