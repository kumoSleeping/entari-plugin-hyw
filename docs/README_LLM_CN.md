# Entari Plugin HYW - LLM 操作手册（中文）

本手册用于约束 `entari-plugin-hyw` 的模型侧行为。

## 1. 输出契约

- 每轮回复必须二选一：
  - `<tool_call ...>...</tool_call>`
  - `<final_response>...</final_response>`
- 同一轮不能同时输出两种标签。
- 信息不确定或有时效要求时，优先调用工具。

## 2. 工具契约

### `web_search`

XML 结构：

```xml
<tool_call name="web_search">
  <query>...</query>
  <kl>...</kl>
  <time_range>...</time_range>
</tool_call>
```

参数约定：

- `query`（必填）：简洁检索词。
- `kl`（可选）：地区/语言代码，如 `us-en`、`cn-zh`。
- `time_range`（策略层必填）：`a/d/w/m/y` 之一。
  - `a`：全时段（仅模型侧占位）。
  - `d/w/m/y`：近1日/近1周/近1月/近1年。

运行时说明：

- 后端在请求前会清理无效或空值。
- `a` 会被归一化为“不加 `df` 参数”。

时效规则：

- 只要本轮使用 `web_search`，至少一条检索使用 `d` 或 `w`。

地区规则：

- 默认不限制 `kl` 以保证召回。
- 当问题明显强地区相关时再加 `kl` 提升精度。

## 3. 最终回复规则

- 用户可见结果必须写在 `<final_response>...</final_response>` 中。
- 不在最终正文里输出流程话术（如“已搜索完毕，下面总结”）。
- 结论应简洁、基于证据。

## 4. 当前仓库适用范围

本仓库有 3 个项目，目前仅 `entari-plugin-hyw` 可作为主要目标：

- `entari-plugin-hyw`：可用且持续开发。
- `hyw-desktop`：开发中。
- `entari-plugin-codex`：开发中。

## 5. 当前配置面

`plugins.entari_plugin_hyw` 现有配置项：

- `api_key`
- `base_url`
- `model_name`
- `temperature`
- `question_command`
- `web_command`（预留字段，当前未绑定处理）
- `headless`
- `quote`
- `theme_color`
