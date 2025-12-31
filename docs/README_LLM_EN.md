# Entari Plugin HYW - LLM Instruction Guide

This document is intended for LLMs to understand how to configure and interact with the **Entari Plugin HYW**.

## 1. Interaction Logic & Usage

### Core Workflow
- **Trigger**: Users use a command prefix (default `/q`).
- **Context**: Supports multi-turn conversations through message replies.
- **Multi-modality**: Supports image analysis. Users can send images with commands or reply to images.
- **Capabilities**: Web search (DuckDuckGo), deep crawling (Crawl4AI), and reasoning.
- **Output**: Responses are rendered as high-quality images with Markdown, code highlighting, LaTeX, and citations.

### Common Commands
- `/q [Query]` - Standard text question.
- `/q [Image] [Query]` - Ask about an image.
- `[quote: User Message] /q` - Reply to another message to ask about it; hyw will extract keywords automatically.

---

## 2. Configuration Guide (entari.yml)

The configuration is located under `plugins.entari_plugin_hyw` in `entari.yml`.

### 2.1 Basic Configurations
These settings determine the fundamental behavior of the plugin.

| Key | Type | Default | Description | Mandatory |
| :--- | :--- | :--- | :--- | :---: |
| `question_command` | `string` | `/q` | Trigger prefix. e.g., if set to `.ask`, use `.ask <query>`. | No |
| `reaction` | `boolean` | `false` | Whether to react with ✨ immediately upon receiving a command. | No |
| `quote` | `boolean` | `true` | Whether to quote the user's original message in the reply. | No |
| `save_conversation` | `boolean` | `false` | Persistence of conversation history to local JSON files. | No |
| `render_timeout_ms`| `integer` | `6000` | Timeout for Markdown-to-Image rendering. | No |
| `fusion_mode` | `boolean` | `false` | (Experimental) Fusion mode switch. | No |
| `max_turns` | `integer` | `10` | Max turns for a single Agent execution. | No |
| `language` | `string` | `Simplified Chinese` | Bot response language (Max 128 chars). Set to "Auto" for automatic detection. | No |

### 2.2 Model Matrix (Multi-Stage)
The plugin uses a 3-stage architecture: **Vision -> Instruct -> Agent**.
**Main Agent** is the final reasoning core. All other stages fallback to these settings if not explicitly configured.

| Key | Type | Description | Mandatory |
| :--- | :--- | :--- | :---: |
| `model_name` | `string` | **Model ID**. e.g., `gpt-4o`, `google/gemini-2.0-flash-exp`. | **Yes** |
| `api_key` | `string` | API Key for the provider. | **Yes** |
| `base_url` | `string` | API base URL. Defaults to `https://openrouter.ai/api/v1`. | No |
| `extra_body` | `object` | Extra parameters for the LLM call (e.g., `{"top_p": 0.8}`). | No |
| `temperature` | `float` | Sampling temperature (Default 0.4). | No |
| `icon` | `string` | Provider icon shown in the UI footer. Supported: `anthropic`, `cerebras`, `deepseek`, `gemini`, `google`, `grok`, `huggingface`, `microsoft`, `minimax`, `mistral`, `nvida`, `openai`, `openrouter`, `perplexity`, `qwen`, `xai`, `xiaomi`, `zai`. If not set, the system will auto-infer from `model_name` (fuzzy matching, e.g., `claude-3-5-sonnet` or `anthropic/...` → `anthropic`; `gpt-4o` or `o1-mini` → `openai`; `gemini-2.0-flash` or `google/...` → `google`; `cerebras/llama-3.3-70b` → `cerebras`). | No |

**Vision & Instruct Independent Configurations**
Use these if you want specific models for vision analysis or intent recognition.
- **If absent**: Falls back to main `model_name`, `api_key`, `base_url`.

| Key | Type | Description |
| :--- | :--- | :--- |
| `vision_model_name` | `string` | Dedicated vision model ID. Highly recommended if the main model lacks vision. |
| `vision_api_key` | `string` | Independent API key for vision stage. |
| `vision_base_url` | `string` | Independent base URL for vision stage. |
| `vision_extra_body` | `object` | Extra parameters for vision model. |
| `vision_icon` | `string` | Custom icon for vision model. |
| `instruct_model_name` | `string` | Model for intent analysis. Use fast models (e.g., Gemini Flash). |
| `instruct_api_key` | `string` | Independent API key for instruct stage. |
| `instruct_base_url` | `string` | Independent base URL for instruct stage. |
| `instruct_extra_body` | `object` | Extra parameters for instruct model. |
| `instruct_icon` | `string` | Custom icon for instruct model. |

### 2.3 Tools and Search
| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `search_provider` | `string` | `crawl4ai` | Backend. `ddgs` (DDG Only), `crawl4ai` (Rec. DuckDuckGo), `httpx` (Rec. Jina AI/SearXNG HTML). |
| `search_limit` | `integer` | `8` | Max search result items to fetch. |
| `headless` | `boolean` | `true` | Browser visibility (for Crawl4AI). |
| `search_base_url` | `string` | *DuckDuckGo Lite* | Search URL (w/ `{query}`). For `httpx`, recommend Jina AI (Get) or SearXNG. |
| `image_search_base_url` | `string` | *DuckDuckGo* | Custom image search Base URL (contains `{query}`). |
| `search_params` | `string` | *None* | Extra search parameters (e.g. `&kl=cn-zh`). |
| `enable_browser_fallback` | `boolean` | `false` | Whether to fallback to crawling if LLM generation fails. |

### 2.4 Billing & Stats
Used to estimate and display costs in the UI. Prices are in **USD per 1 Million Tokens**.

| Key | Type | Description |
| :--- | :--- | :--- |
| `input_price` | `float` | Main model input price per 1M tokens. |
| `output_price` | `float` | Main model output price per 1M tokens. |
| `vision_input_price` | `float` | Vision override price (falls back to `input_price` if null). |
| `vision_output_price` | `float` | Vision override price (falls back to `output_price` if null). |
| `instruct_input_price`| `float` | Instruct override price (falls back to `input_price` if null). |
| `instruct_output_price`| `float` | Instruct override price (falls back to `output_price` if null). |

---

## 3. Troubleshooting & Advice

1.  **Auth & Connection**:
    *   **Auth Failure**: Ensure `api_key` is correct.
    *   **Connection Failure**: Check if `base_url` is the API root (usually ending with `/v1`).
2.  **Vision Conflicts**:
    *   If image processing fails, ask the user if their `model_name` supports Vision; if not, suggest setting `vision_model_name`.
    *   If results are poor, suggest changing `search_provider`.

---

## 4. Real World Config Example

The following is a complete configuration example based on a real production environment.

```yaml
plugins:
  entari_plugin_hyw:
    # --- Basic Behavior ---
    question_command: "/ai"      # Custom trigger command
    headless: true              # Run browser in headless mode
    save_conversation: true     # Save logs for debugging
    reaction: false             # Disable emoji reaction
    quote: false                # Disable reply quoting
    
    # --- Search Config (Custom/Third-party Aggregator) ---
    search_provider: "ddgs"
    search_limit: 12

    # --- Main Agent ---
    # Using Cerebras (Text-only, extremely fast)
    model_name: "qwen-3-235b-a22b-instruct-2507"
    api_key: "csk-..."
    base_url: "https://api.cerebras.ai/v1"
    icon: "cerebras"

    # --- Vision ---
    # Main model lacks vision, so config Gemini Flash specifically for images
    vision_model_name: google/gemini-3-flash-preview
    vision_api_key: "sk-or-v1-..."
    vision_icon: "gemini"

    # --- Instruct ---
    # Not configured, automatically fallback to Main Agent
```
