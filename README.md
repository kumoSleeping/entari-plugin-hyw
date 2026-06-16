# entari-plugin-hyw

HYW v6 is a single Entari plugin package. The repository root is the plugin package; there is no `src/`, bundled Codex plugin, desktop app, or `core/` wrapper.

## Configure

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
```
