# NDI Data Browser v2

[![CI](https://github.com/Waltham-Data-Science/ndi-data-browser-v2/actions/workflows/ci.yml/badge.svg)](https://github.com/Waltham-Data-Science/ndi-data-browser-v2/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](https://react.dev/)

Cloud-first neuroscience data browser for NDI Cloud datasets. No SQLite dataset storage; every read hits the cloud directly.

## Architecture

- **Browser** — React 19 + Vite 8 + TanStack Query + React Router 7
- **Backend** — FastAPI proxy/enricher over ndi-cloud-node (Python 3.12, httpx HTTP/2)
- **Sessions** — Redis, encrypted Cognito access + refresh tokens, httpOnly cookie
- **Enrichment** — binary decoding (NBF/VHSB/image/video), ontology term cache (ephemeral SQLite)
- **Stateless backend** — no dataset volume, no local caches that matter

See:
- [docs/architecture.md](docs/architecture.md)
- [docs/workflows.md](docs/workflows.md)
- [docs/error-catalog.md](docs/error-catalog.md)
- [docs/operations.md](docs/operations.md)
- ADRs under [docs/adr/](docs/adr/)

## Local development

```bash
# Start Redis + the app
docker-compose -f infra/docker-compose.yml up -d

# Backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env       # fill in NDI_CLOUD_URL, SESSION_ENCRYPTION_KEY
uvicorn app:app --reload --port 8000

# Frontend (separate shell)
cd frontend
npm install
npm run dev
```

## Milestones

| M | Name | Gate |
|---|---|---|
| 0 | Foundation | CI green, observability wired, docs drafted |
| 1 | Cloud client + auth | All endpoints callable, refresh flow tested |
| 2 | Public read paths | Unauthenticated browsing E2E |
| 3 | Authenticated reads | Login + private browsing E2E |
| 4 | Summary tables | Single + combined via cloud, <3s |
| 5 | Query + cross-cloud | Scope selector, negation, ontology cross-linking |
| 6 | Hardening | Load + soak + chaos clean |
| 7 | Cutover | Feature-flagged ramp to 100% |

## Tests

```bash
# Backend
cd backend && pytest
# Frontend
cd frontend && npm test
# E2E
cd frontend && npm run test:e2e
```

## License

Same as v1 — see [LICENSE](LICENSE).
