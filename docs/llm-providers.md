# LLM providers

The AI-backed tools talk to a model provider through one abstraction
(`core/llm.py`). v0.1.0 wires up the local provider; the hosted providers carry
configuration and report health, and their completion paths become functional in
v0.2.0.

## Supported providers

| Provider  | Kind   | Default model         | Status in v0.1.0          |
|-----------|--------|-----------------------|---------------------------|
| Ollama    | local  | `llama3.1:8b`         | Fully functional          |
| Anthropic | hosted | `claude-sonnet-4-5`   | Config and health only    |
| OpenAI    | hosted | `gpt-4o`              | Config and health only    |
| Gemini    | hosted | `gemini-2.0-flash`    | Config and health only    |

Embeddings are produced locally with Ollama using `nomic-embed-text` (768
dimensions). The embedding model is independent of the completion model.

## API keys

Keys resolve in this order: the value in `config.toml`, then the matching
environment variable, then empty (which leaves the provider disabled).

| Provider  | Environment variable |
|-----------|----------------------|
| Anthropic | `ANTHROPIC_API_KEY`  |
| OpenAI    | `OPENAI_API_KEY`     |
| Gemini    | `GEMINI_API_KEY`     |

A key supplied through the environment is used at runtime but never written to
`config.toml`.

## Configuration

The relevant section of `~/.yttools/config.toml`:

```toml
[llm]
default_provider = "ollama"
default_model = "llama3.1:8b"
concurrent_requests = 2
embedding_model = "nomic-embed-text"

[llm.ollama]
base_url = "http://localhost:11434"

[llm.anthropic]
api_key = ""
default_model = "claude-sonnet-4-5"

[llm.openai]
api_key = ""
default_model = "gpt-4o"

[llm.gemini]
api_key = ""
default_model = "gemini-2.0-flash"
```

The Settings page in the web UI edits these values and tests each provider's
connection.

## Local setup

Install [Ollama](https://ollama.com), then pull a completion model and the
embedding model:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```
