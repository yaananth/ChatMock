<div align="center">
  <h1>ChatMock</h1>
  <p><b>OpenAI compatible API powered by your ChatGPT plan.</b></p>
  <p>Use your ChatGPT Plus/Pro account to call OpenAI models from code or alternate chat UIs.</p>
  <br>
</div>

## What It Does

ChatMock runs a local server that creates an OpenAI compatible API, and requests are then fulfilled using your authenticated ChatGPT login with the oauth client of Codex, OpenAI's coding CLI tool. This allows you to use GPT-5 and other models right through your OpenAI account, without requiring an api key.

## Quickstart

- Clone or download this repository.
- You need a paid ChatGPT account (Plus/Pro).
- In the project directory:

1. Sign in with your ChatGPT account and follow the prompts
```bash
python chatgpt_local.py login
```

2. After the login completes successfully, you can just simply start the local server

```bash
python chatgpt_local.py serve
```

- Then, you can simply use the address and port as the baseURL as you require (http://127.0.0.1:8000 by default)

# Examples

### Python 

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="key"  # ignored
)

resp = client.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "hello world"}]
)

print(resp.choices[0].message.content)
```

### curl

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5",
    "messages": [{"role":"user","content":"hello world"}]
  }'
```

# What's supported

- Tool calling
- Vision/Image understanding
- Thinking summaries (through thinking tags)

## Notes & Limits

- Requires an active, paid ChatGPT account.
- Expect lower rate limits than what you may recieve in the ChatGPT app.
- Must use the instrunctions given in the prompt.md, instrunctions in the system prompt cannot be modified.
- Use responsibly and at your own risk. This project is not affiliated with OpenAI, and is a educational exercise.

# Supported models
- `gpt-5`
- `codex-mini`

# Customisation / Configuration

### Thinking effort

GPT-5 has a configurable amount of "effort" it can put into thinking, which may cause it to take more time for a response to return, but may overall give a smarter answer. Applying this parameter after `serve` forces the server to use this reasoning effort by default, unless overrided by the API request with a different effort set. The default reasoning effort without setting this parameter is `medium`.
`--reasoning-effort` (choice of low,medium,high)

### Thinking summaries

Models like GPT-5 do not return raw thinking content, but instead return thinking summaries. These can also be customised by you.
`--reasoning-summary` (choice of auto,concise,detailed,none)

## Notes
If you wish to have the fastest responses, I'd recommend setting `--reasoning-effort` to low, and `--reasoning-summary` to none.
All parameters and choices can be seen by sending `python chatgpt_local.py serve --h`

**When the model returns a thinking summary, the model will send back thinking tags to make it compatiable with chat apps. If you don't like this behavior, you can instead set `--reasoning-compat` to legacy, and reasoning will be set in the reasoning tag instead of being returned in the actual response text.**

# Todo
- Implement Ollama support (?)
- Explore to see if we can make more model settings accessible
- Implement analytics (token counting, etc, to track usage)
