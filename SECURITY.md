# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in this project, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Contact the maintainers directly via the organization's private channels.

## Credential Management

This project enforces a strict no-hardcoded-credentials policy:

- All API keys, tokens, and secrets MUST be loaded from environment variables.
- The `.gitignore` excludes `.env` files, credential stores, and session state.
- Pre-commit hooks should be configured to scan for accidental credential commits.

## Required Environment Variables

| Variable | Description |
| :--- | :--- |
| `GEMINI_API_KEY` | Google AI Studio or Vertex AI API key |
| `TAILSCALE_AUTHKEY` | (Optional) Tailscale authentication key |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | (Optional) GitHub API token for MCP |
| `TELEGRAM_BOT_TOKEN` | (Optional) Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | (Optional) Telegram chat ID for notifications |

## Transport Security

All network communication between the mobile client and host daemon traverses
an encrypted WireGuard tunnel via Tailscale. No plaintext traffic is permitted.

## Execution Security

The daemon enforces a strict policy engine (`policy.toml`) that validates all
tool calls against an allowlist before execution. Commands targeting system
directories or unlisted binaries are rejected.
