# wenqu-cli

`wenqu` is the runtime companion for Wenqu Skills. It owns executable
capabilities, runtime setup and external integrations; the skills themselves
remain focused on editorial workflows and decision rules.

The first migrated capability is `library`: multi-engine candidate discovery
and browser-backed material downloads. It replaces the separate
`open-websearch` and `crwl` command contracts with a single Python CLI.

## Install

```bash
pipx install wenqu-cli
wenqu setup library
```

The package installs its Python dependencies with Wenqu. `wenqu setup library`
explicitly downloads Chromium for Crawl4AI; this separate command makes the
large browser download visible and consent-based.

## Optional credentials

Run this once to create the local, owner-only credentials template:

```bash
wenqu config init
wenqu config status
```

To enable Exa, edit the printed `credentials.env` path and set
`EXA_API_KEY=...`. The file is created with mode `600`, is never committed, and
Wenqu never prints the key. You can instead provide `EXA_API_KEY` in the command
environment. If Exa is selected without a key, Wenqu skips it before making a
network request and returns an explicit `skippedEngines` entry.

## Library commands

```bash
# Inspect dependencies and managed directories.
wenqu doctor --json

# Preview first-run browser setup without downloading anything.
wenqu setup library --dry-run

# Search all ten managed channels, preserving partial failures in JSON.
wenqu library search "agent memory system" --limit 12 --json

# Select only relevant channels for Chinese technical articles.
wenqu library search "智能体记忆 源码解析" \
  --engines baidu,juejin,csdn --limit 9 --json

# Download a page as Markdown; browser rendering is provided by Crawl4AI.
wenqu library fetch https://example.com/article --out materials/articles/example.md

# Bounded same-origin collection. The output is one Markdown file separated by URLs.
wenqu library fetch https://docs.example.com --max-pages 8 --out materials/docs/example.md

# Download a confirmed public WeChat article. Wenqu uses a mobile WeChat UA
# and waits for the article body before writing Markdown.
wenqu library fetch 'https://mp.weixin.qq.com/s?...' \
  --out materials/articles/wechat/article.md --json
```

`wenqu library search` supports `baidu`, `bing`, `linuxdo`, `csdn`,
`duckduckgo`, `exa`, `brave`, `juejin`, and `sogou`. Exa requires
`EXA_API_KEY`; it is skipped with a structured configuration hint when no key is present.
For Baidu, Bing, Brave and Sogou, direct-search failures can be retried through
Wenqu's Crawl4AI adapter. CSDN discovery searches public indexes with a CSDN
keyword and keeps only CSDN-domain results, instead of calling CSDN's protected
search endpoint. Pass `--no-browser-fallback` to disable browser retries.
WeChat page downloads automatically use the required mobile UA and wait for
`#js_content`; access controls and verification pages are reported as failures
rather than retried or bypassed.

When Baidu returns an access-verification page, Wenqu reports
`ACCESS_CHALLENGE` rather than silently treating it as an empty result. LinuxDo
candidate discovery uses `site:linux.do` through DuckDuckGo, Bing, then Brave;
it does not call LinuxDo's challenged unauthenticated search endpoint.

Set `WENQU_PROXY_URL` for a runtime HTTP proxy, or configure the same value in
`~/.gogoingai/wenqu-skills/config.json`:

```json
{
  "library": {
    "proxyUrl": "http://127.0.0.1:7890"
  }
}
```

Wenqu shares the existing `~/.gogoingai/wenqu-skills/` root: non-secret
configuration is `config.json`, private credentials are `credentials.env`,
runtime state is in `runtime/`, and cached data is in `cache/`.

## Image commands

```bash
# Check Codex, PicGo, and configured image providers without printing secrets.
wenqu image doctor --json

# Create image defaults and a private provider credentials template.
wenqu image config-init

# Generate one image. Provider can be codex, openai, openrouter, dashscope, or seedream.
wenqu image generate --provider codex --ar 16:9 \
  --prompt "一张中文技术架构示意图" --out /tmp/diagram --timeout-sec 300

# `--image` is kept as an alias for `--out`; use a one-off private credentials file when needed.
wenqu image generate --provider openai --prompt "一张技术示意图" \
  --image /tmp/diagram --env-file /secure/provider-credentials.env

# Download one versioned Wenqu style reference into the managed cache.
wenqu image fetch-ref mono-marker/mono-marker-03-layered-arch.png
```

Image-specific non-secret defaults live in `~/.gogoingai/wenqu-skills/image/config.json`;
provider credentials live separately in `~/.gogoingai/wenqu-skills/image/credentials.env`
with mode `600`. `--env-file` can select another owner-only credentials file;
its entries never override credentials already supplied by the process environment.
