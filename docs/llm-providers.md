# LLM providers

The AI-backed tools talk to a model provider through one abstraction
(`core/llm.py`). All four providers are wired: the local Ollama provider and the
three hosted providers, each calling its vendor's REST API directly over httpx
(no vendor SDKs). Completion, streaming, and JSON mode are supported everywhere.

## Supported providers

| Provider  | Kind   | Default model         | Completion | Embeddings        |
|-----------|--------|-----------------------|------------|-------------------|
| Ollama    | local  | `llama3.1:8b`         | Yes        | Yes               |
| Anthropic | hosted | `claude-sonnet-4-5`   | Yes        | No (use Ollama)   |
| OpenAI    | hosted | `gpt-4o`              | Yes        | Yes               |
| Gemini    | hosted | `gemini-2.0-flash`    | Yes        | Yes               |

By default, embeddings are produced locally with Ollama using `nomic-embed-text`
(768 dimensions). The embedding model is independent of the completion model.
Anthropic ships no embeddings endpoint, so embedding requests there fall back to
Ollama.

The default models above are starting points. Pick a current model for your
account on the Settings page; "Test connection" confirms the key works and fills
the model picker with the models your account can use.

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
