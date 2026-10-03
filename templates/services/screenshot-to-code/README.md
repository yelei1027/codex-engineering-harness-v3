# screenshot-to-code optional sidecar

Backend and frontend run in separate Docker containers from commit
`d026163f586dfa8c5c10d28c36edd59a9d3b0e88`. Backend dependencies use upstream
`poetry.lock`, Poetry 1.8.0, and a project-local Python environment inside Docker.
Playwright Chromium and its Linux dependencies are installed in the backend image.
Frontend uses `pnpm@10.32.1` and `pnpm install --frozen-lockfile` with upstream `pnpm-lock.yaml`.
Base image digests are fixed in both runtime Dockerfiles; upstream source is preserved.

From the Harness root:

```bash
python3 scripts/services-build.py --service screenshot-to-code
python3 scripts/services.py start --service screenshot-to-code
python3 scripts/services.py status --service screenshot-to-code
python3 scripts/services.py stop --service screenshot-to-code
```

The machine binding records frontend `http://127.0.0.1:15173` and backend
`http://127.0.0.1:17001`. Frontend proxies `/api` and `/generate-code` to the backend
on its private Docker network, including WebSocket traffic. Host ports always bind to loopback.
`SCREENSHOT_BACKEND_PORT` and `SCREENSHOT_FRONTEND_PORT` select publish ports when starting Compose;
update machine binding if changing them.

The protected `.env` file is mode 600. Add one authorized `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` there and restart/recreate the backend for real code generation.
`REPLICATE_API_KEY` is optional for image features. No key was installed or copied from global configuration.
Health, frontend proxy, Chromium rendering, and a local HTML screenshot fixture are verified.
Screenshot-to-model-to-code-to-preview is `OPTIONAL_PROVIDER_BLOCK`; no generation success is claimed.

Named volumes `codex-harness-screenshot-logs` and `codex-harness-screenshot-assets` retain
local runtime data and are preserved by stop. Keep them out of distribution packages.
