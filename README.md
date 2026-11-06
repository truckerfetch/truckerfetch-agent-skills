# TruckerFetch agent skills

Official reusable skills for U.S. motor carrier, operating authority and observed insurance research through TruckerFetch. [Documentation](https://truckerfetch.com/docs) · [API examples](https://github.com/truckerfetch/truckerfetch-api-examples) · [MCP integration](https://github.com/truckerfetch/truckerfetch-mcp).

## Install

With the [open skills CLI](https://github.com/vercel-labs/skills) (Node.js 22.20+):

```bash
npx skills add truckerfetch/truckerfetch-agent-skills --skill truckerfetch
```

Alternatively, copy `skills/truckerfetch` into your agent's skills directory. In Codex, invoke `$truckerfetch` for a targeted workflow. The repository does not install an API key or grant a subscription.

Programmatic data requires active Pro and appropriate scopes. Prefer a connected TruckerFetch MCP tool when available. The hosted endpoint `https://truckerfetch.com/api/mcp` uses OAuth; the bundled Python helper and local MCP bridge use a REST API key in `TRUCKERFETCH_API_KEY`. Keep credentials outside source control. Do not use a REST key as a hosted MCP token.

The skill preserves source attribution, separate insurance date bases, account-owner historical approvals, export authorization and durable idempotency. It includes a Python 3.10+ standard-library client, with one network attempt per call, HTTPS, no authenticated redirects, bounded responses and sanitized error reporting. It is a helper, not a complete SDK or a local copy of the carrier dataset.

## Verify

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile skills/truckerfetch/scripts/client.py
```

All tests use synthetic credentials and records. No subscription or live API key is required.

The MIT license applies to this client code and documentation. TruckerFetch names/artwork and the hosted data/API remain subject to their respective rights and service terms; the license does not grant dataset access or redistribution rights.
