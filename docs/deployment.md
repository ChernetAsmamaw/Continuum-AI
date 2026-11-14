# Deploying Continuum

Continuum ships as **one container** (FastAPI backend + built React console)
plus **MongoDB Atlas** (or an optional local MongoDB via the compose profile).
`docker compose up -d --build` on a VPS is the whole deployment.

## 1. Prerequisites

- A Linux VPS (2 GB RAM is plenty) with Docker + Compose, **and** Tailscale
  installed **if** you keep the LLM on the tailnet-only gateway (see §5).
- A MongoDB Atlas cluster (M0 free tier is fine to start).
- A public HTTPS route to the container (Tailscale Funnel, Caddy, nginx, or
  any reverse proxy).
- The `LLM_API_KEY` for whatever OpenAI-compatible gateway you use for
  extraction.

## 2. Configure `.env`

Copy the values you'd use locally into a `.env` **on the server** (never
commit it):

```env
# MongoDB (Atlas)
CONTINUUM_DB_MODE=atlas
MONGODB_URI=mongodb+srv://user:pass@cluster.x.mongodb.net/continuum?tls=true

# LLM (OpenAI-compatible endpoint used for document extraction)
LLM_PROVIDER=openai
LLM_MODEL=qwen3.8-27b
LLM_BASE_URL=https://<gateway>/v1
LLM_API_KEY=sk-...
LLM_DISABLE_THINKING=true

# Voice (ElevenLabs) — only needed for live calls
ELEVENLABS_API_KEY=sk_...
ELEVENLABS_AGENT_ID=agent_xxx
CONTINUUM_VOICE_SECRET=<long random string>
# ELEVENLABS_WEBHOOK_SECRET=...   (if webhook signing is enabled)

# Auth
AUTH_SECRET=<long random string>      # sign JWTs; required in production
# DEMO_COORDINATOR_EMAIL / _PASSWORD / _NAME, DEMO_CLINICIAN_* (see .env.example)

# PHI retention
PHI_RETENTION_DAYS=365

# Posture
DEMO_MODE=false                        # disables /api/demo/* endpoints
ENVIRONMENT=production
CORS_ORIGINS=https://your.domain       # if the console is served from another origin
```

Generate the secrets: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.

## 3. Build & run

```bash
git clone <repo> && cd continuum
# put .env in place
docker compose up -d --build
docker compose logs -f continuum        # wait for "Continuum ready"
```

Verify:

- `https://<host>/api/health` → `{"status":"ok","database":"connected","llm":true,...}`
- `https://<host>/` → the console (sign in with the coordinator credentials).

On first boot the app creates indexes, seeds the coordinator + clinician demo
users (change the `DEMO_*` settings or delete the synthetic users for real
use), and seeds the demo patients if the DB is empty.

## 4. Reverse proxy / HTTPS

### Tailscale Funnel (simplest, private tailnet exposure)

```bash
tailscale funnel 8000
```

Use the printed `https://<node>.<tailnet>.ts.net` as your public URL. Funnel
gives you TLS; the app behind it is plaintext HTTP.

### Caddy (plain VPS, automatic TLS)

```
continuum.clinic.example {
    reverse_proxy 127.0.0.1:8000
}
```

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name continuum.clinic.example;
    # ssl_certificate ...
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;     # LLM extraction can take 10–30s
    }
}
```

The app is stateless with respect to the load balancer (all state is in
MongoDB + LangGraph checkpoints), so scaling out to multiple containers is
possible later; a single container is correct for a clinic pilot.

## 5. The LLM gateway

The default gateway in this project (`du-ai.*.ts.net:8443`) is a **Tailscale
Funnel-only vLLM instance** — it is only reachable from inside the tailnet.

Options for a clinic deployment:

1. **Install Tailscale on the VPS** and keep using the gateway. The container
   reaches it at `https://du-ai.<tailnet>.ts.net:8443/v1`. (Funnel URLs are
   actually reachable from anywhere the DNS resolves — the gateway is public
   once funnel is on; the tailnet is only needed for `tailscale` service
   addresses.)
2. **Point `LLM_BASE_URL` at any OpenAI-compatible endpoint** (OpenAI,
   Groq, self-hosted vLLM behind your own reverse proxy) with a matching
   `LLM_API_KEY` and `LLM_MODEL`. No code changes needed.

If the LLM is unreachable the app still boots and degrades: document
extraction falls back to a deterministic parser and memory recall is lexical.

## 6. Voice (optional)

Follow `docs/elevenlabs-setup.md` to create the ElevenLabs agent. The agent's
tools and post-call webhook must reach `https://<host>/api/voice/*` and carry
`x-continuum-token: <CONTINUUM_VOICE_SECRET>`. The endpoint is token-gated, so
it is safe to expose publicly.

## 7. Operations

| Task | How |
|---|---|
| Logs | `docker compose logs -f continuum` |
| Update | `git pull && docker compose up -d --build` (rolling; state is in Atlas) |
| Reset demo data | sign in → `POST /api/demo/seed?reset=true` (only while `DEMO_MODE=true`) |
| Retention dry-run | `GET /api/admin/retention` (coordinator) |
| Apply retention | `POST /api/admin/retention/apply` (coordinator) |
| Backups | Atlas automated backups (or `mongodump` of the `continuum` DB nightly) |

## 8. Security checklist before a clinic uses it

- [ ] `AUTH_SECRET` set to a long random value (not the dev default).
- [ ] `CONTINUUM_VOICE_SECRET` set (voice endpoints reject otherwise).
- [ ] `DEMO_MODE=false` if the demo runner/seed endpoints must not be reachable.
- [ ] Atlas IP allowlist / private endpoint; TLS enforced (it is by default).
- [ ] HTTPS in front of the container (TLS terminates at the proxy).
- [ ] Change or remove the synthetic `DEMO_*` accounts; create real users.
- [ ] Review `docs/compliance.md` (BAA list, PHI inventory, retention).
