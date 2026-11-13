# Security Policy

## Security Baseline

nextoomio is designed for local-network operation without mandatory cloud dependencies. Each release delivers the security controls required by its features ([SPECIFICATION.md](SPECIFICATION.md) section 10, [ARCHITECTURE.md](ARCHITECTURE.md) section 3.7):

- **First-Start Administrator Setup:** No universal default password. Until setup completes, only `/api/v1/setup/*` is accessible. Passwords are hashed with Argon2id.
- **Authenticated Control Plane:** HTTPS (`TLS 1.3` / local certificate), `HttpOnly` + `Secure` + `SameSite=Strict` cookies with CSRF tokens for browser sessions, and revocable bearer tokens for API clients.
- **Least-Privilege Architecture:** `nextoomio-core` runs unprivileged. Privileged system operations (slot installation, NAS mounts, hardware profile selection, firewall updates) are isolated in `nx-sysd` over a local Unix socket with strict input validation and no shell execution.
- **Secret Isolation:** Secrets are stored under `/data/secrets/v<N>/<service>/` (`0700`), wrapped in redacting memory types, and excluded from logs, events, MQTT state, and diagnostic exports.
- **Authenticated Native Multiroom (`v0.0.2+`):** TLS 1.3 with provisioned master public-key (SPKI) pins and TLS-exporter-bound HKDF/HMAC proofs (`EXPORTER-nextoomio-group-v1`) on both control and generation-bound audio tunnels.

## Reporting a Vulnerability

Please report security issues privately via GitHub Security Advisories on the repository rather than opening a public issue containing exploit details.
