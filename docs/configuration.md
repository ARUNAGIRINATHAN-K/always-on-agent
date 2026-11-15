# Configuration & Model Selection

The agent reads configuration from environment variables or a `.env` file located in the workspace root.

---

## Environment Variables Reference

| Variable | Type | Default | Description |
|---|:---:|:---:|---|
| `OLLAMA_BASE_URL` | string | `http://localhost:11434` | The HTTP endpoint for your Ollama daemon. When running via Docker Compose, use `http://ollama:11434`. |
| `OLLAMA_MODEL` | string | `llama3.2` | The local model tag. Examples: `llama3.2`, `llama3.1:8b`, `qwen2.5:7b`, `mistral:7b`. |
| `AGENT_DATA_DIR` | string | `data` | Directory where SQLite state (`agent.db`) and markdown files (`notes/`) are stored. |
| `AGENT_TIMEZONE` | string | `Asia/Kolkata` | IANA timezone string used for cron schedule calculations and run date deduplication. |
| `AGENT_MAX_STEPS` | integer | `8` | Upper bound on how many steps a single plan may contain. |
| `AGENT_TOOL_TIMEOUT` | integer | `20` | Timeout in seconds for an individual tool step execution. |
| `AGENT_TOOL_RETRIES` | integer | `2` | Maximum retry attempts with exponential backoff on transient tool failures. |
| `AGENT_LLM_TIMEOUT` | integer | `120` | Timeout in seconds when waiting for Ollama to produce a plan. |

---

## Model Selection Recommendations

The agent's plan quality depends directly on your local LLM's instruction-following and JSON formatting capabilities.

### Recommended Models by Hardware Tier

| Model Tag | Parameter Size | Minimum VRAM / RAM | Strengths & Notes |
|---|:---:|:---:|---|
| `llama3.2` *(Default)* | 3B | 4 GB | Ultra-fast planning and low memory footprint. Follows minimal JSON plans reliably with the included few-shot prompt. |
| `qwen2.5:7b` | 7B | 8 GB | Exceptional structured JSON adherence and tool argument parameter precision. Ideal for more complex goals. |
| `llama3.1:8b` | 8B | 8 GB | High general reasoning and comprehensive markdown synthesis. Great for detailed research and document drafting. |
| `mistral:7b` | 7B | 8 GB | Strong concise instruction-following. |

To switch models:
```bash
# 1. Pull the model in Ollama
ollama pull qwen2.5:7b

# 2. Update .env
echo "OLLAMA_MODEL=qwen2.5:7b" >> .env
```
