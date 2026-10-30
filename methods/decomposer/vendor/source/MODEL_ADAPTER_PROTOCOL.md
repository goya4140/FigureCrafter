# Model adapter protocol

The method is model-independent. A model backend receives the same stage prompt, source image, input JSON, and strict output schema; only the transport changes.

## `codex-cli`

Use this backend for GPT-Terra, GPT-Astra, or another model exposed by the Codex CLI. The framework supplies the image, schema, reasoning effort, read-only sandbox, disabled tools, and isolated temporary workspace.

## `external-json`

Use this backend for DeepSeek or another external vision model. `model.command` may be a string or string array. `model.arguments` supports four placeholders:

- `{stage}`
- `{prompt_file}`
- `{schema_file}`
- `{image_file}`

The process receives the complete prompt on stdin and must write only the final JSON object to stdout. Diagnostics belong on stderr. A non-zero exit, invalid JSON, or timeout is recorded as degraded model evidence and does not silently become a successful model call.

Credentials are inherited only when explicitly listed in `model.environment_allowlist`. They are never written to run metadata.

`adapters/openai_compatible_vision.py` is a reference adapter. Providers differ in vision payload and structured-output support, so choose `json_schema`, `json_object`, or `prompt_only` according to the endpoint being tested.

For DeepSeek V4.1 Flash, use `configs/models/deepseek-v4.1-flash.json`:

- model ID `deepseek-flash`;
- Chat Completions `image_url` with original detail;
- `response_format={"type":"json_object"}` plus the complete JSON Schema in the system instruction;
- fixed reasoning effort and output-token limit recorded in the profile;
- one retry only when the provider returns empty JSON content.

Each successful or empty response writes a redacted provider metadata line to
stderr containing attempt number, model, finish reason, token usage, and system
fingerprint. It never records authorization headers, raw image bytes, or hidden
reasoning content.
