# Hermes dashboard fleet rollout notes

Use this when exposing Hermes dashboards as public mini-apps across fleet machines.

## Naming and auth

- Dashboard slug convention: `/hermes-$botname/` using the bot/persona name, not the
  owner name. Examples: `/hermes-atlas/`, `/hermes-scout/`, `/hermes-vega/`.
- Public dashboards are admin UIs. They must be password-gated through auth-service;
  never leave a Hermes dashboard passwordless on a Funnel-exposed host.
- For auth-service env vars, slug `hermes-atlas` becomes `APP_PASSWORD_HERMES_ATLAS`,
  `APP_TITLE_HERMES_ATLAS`, and `APP_DESC_HERMES_ATLAS` (uppercase, hyphens to
  underscores).

## Inspect live layout before editing

Fleet machines drift between router layouts. Do not assume the skill's default path.

- Newer layout: `~/mini-apps/ecosystem.config.js` + `~/mini-apps/router/Caddyfile`.
- Older layout: `~/openclaw-apps/ecosystem.config.js` +
  `~/openclaw-apps/_registry/Caddyfile`.
- The canonical answer on a live host is PM2's caddy process args (`pm2 jlist` /
  `pm2 list`) plus `tailscale serve status`.

Always back up the live ecosystem/Caddyfile before replacing or patching them.

## Public Funnel + auth routing trap

Password-gated mini-apps need both the app slug and `/auth/*` reachable through the same
Caddy/auth-service front door. A path-only Tailscale Funnel route such as:

```bash
tailscale funnel --https=443 --set-path=/hermes-hex http://127.0.0.1:8080/hermes-hex
```

can fail because the dashboard redirects to `/auth/login?...`; if `/auth/*` still falls
through to an existing gateway/root route, login breaks.

Preferred shape for a host that already has public root routes:

1. Make Caddy/app-router on `127.0.0.1:8080` the single public front door.
2. Put `handle /auth/*` and `handle /hermes-$botname/*` in that Caddyfile.
3. Preserve existing root/hooks by proxying them from Caddy to their old upstreams.
4. Point the public Funnel root to Caddy:
   `tailscale funnel --bg --https=443 http://127.0.0.1:8080`.

This keeps auth redirects, dashboard assets, root gateway behavior, and webhook paths
under one hostname.

## Hermes dashboard process pitfalls

- Use `X-Forwarded-Prefix /hermes-$botname` and still
  `uri strip_prefix /hermes-$botname`.
- If `hermes dashboard --skip-build` exits with `no web dist found`, either build the
  web UI once or drop `--skip-build` for the first start so Hermes builds
  `hermes_cli/web_dist`.
- Verify the dashboard is pointed at the state DB that actually has sessions. Some
  agents use flat `~/.hermes/state.db`; profiled agents use
  `~/.hermes/profiles/<profile>/state.db`.

## Linux host notes

- `tailscale funnel` may require sudo unless `tailscale set --operator=$USER` has been
  configured.
- If installing distro Caddy on Linux but supervising Caddy with PM2, disable the distro
  service (`sudo systemctl disable --now caddy.service`) so it does not fight
  PM2-managed Caddy.
- An apt transaction can return non-zero because of unrelated pre-existing broken
  packages even after installing Caddy. Check `command -v caddy && caddy version` before
  retrying or changing strategy.

## Verification standard

For each public dashboard, verify all of these before declaring done:

1. PM2 process(es) online: auth-service, caddy, dashboard.
2. Local route returns login redirect/login page:
   `http://127.0.0.1:8080/hermes-$botname/`.
3. Public URL reaches the auth login page.
4. Password login succeeds.
5. Authenticated dashboard renders (`Hermes Agent - Dashboard`, nav/sessions visible).
6. Existing root/hook routes on that host still have intentional Caddy/Tailscale routing
   after the change.
