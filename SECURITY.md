# Security Policy

## Supported Versions

Security fixes target the current `main` branch unless release branches are created later.

## Reporting A Vulnerability

Please report suspected vulnerabilities privately first. If GitHub Security Advisories are enabled for the repository, use that channel. Otherwise contact the maintainer directly through the repository owner profile.

Do not open a public issue containing exploit details, API keys, private model endpoints, credentials, or vulnerable deployment URLs.

## Security-Relevant Areas

- WebUI and Admin UI HTTP endpoints
- Session cookies and per-user worker isolation
- File uploads for masks, recipes, PDFs, and exports
- Runtime storage under `TCAD_Web_Data/`
- Optional LLM provider configuration and API keys
- Optional PDF/GDS parsers

## Operational Notes

- Bind the WebUI only on trusted networks unless you have reviewed authentication and firewall rules.
- Set an Admin UI password when exposing the Admin UI beyond localhost.
- Do not commit `LLM_Test_Config.json`, `.env`, API keys, local literature databases, or generated `TCAD_Web_Data/` contents.
- Treat imported recipes, masks, PDFs, and GDS files as untrusted input.
