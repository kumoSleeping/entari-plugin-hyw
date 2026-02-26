# Entari Plugin HYW - LLM Operation Guide (EN)

This guide is for model-side behavior when serving `entari-plugin-hyw`.

## 1. Core Contract

- Every assistant turn must output **either** `<tool_call ...>...</tool_call>` **or** `<final_response>...</final_response>`.
- Do not output both in the same turn.
- If information is uncertain or freshness-sensitive, prefer using tools.

## 2. Tool Contract

### `web_search`

XML shape:

```xml
<tool_call name="web_search">
  <query>...</query>
  <kl>...</kl>
  <time_range>...</time_range>
</tool_call>
```

Parameters:

- `query` (required): concise search phrase.
- `kl` (optional): locale/region code such as `us-en`, `cn-zh`.
- `time_range` (required by policy): one of `a/d/w/m/y`.
  - `a`: all time (model-facing placeholder).
  - `d/w/m/y`: recent day/week/month/year.

Runtime note:

- Backend strips unsupported or empty values before sending requests.
- `a` is normalized to “no `df` filter” at request time.

Freshness rule:

- If `web_search` is used in the turn, ensure **at least one query** uses `d` or `w`.

Region rule:

- Keep `kl` empty by default for recall.
- Set `kl` when the user context or task is clearly region-bound.

## 3. Final Response Rules

- Final user-visible content must be inside `<final_response>...</final_response>`.
- Keep process narration out of final output (no “I searched and now summarize” boilerplate).
- Use concise, evidence-based wording.

## 4. Current Runtime Scope

This repository contains three projects, but only `entari-plugin-hyw` is the active usable target.

- `entari-plugin-hyw`: active.
- `hyw-desktop`: in development.
- `entari-plugin-codex`: in development.

## 5. Config Surface (Current)

`plugins.entari_plugin_hyw` keys currently in use:

- `api_key`
- `base_url`
- `model_name`
- `temperature`
- `question_command`
- `web_command` (reserved, currently not wired)
- `headless`
- `quote`
- `theme_color`
