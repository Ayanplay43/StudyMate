# Publish StudyMate with HTTPS (VPS + domain)

**Status: deployment-ready configuration, not deployed. No public URL has been created.**

1. Obtain a Linux VPS with a public IPv4 address, a domain/subdomain, and outbound SMTP access. Point the domain's A record at the VPS.
2. Install Docker Engine and Docker Compose plugin. Ensure inbound TCP ports 80 and 443 are open. Do not expose 8000 or the SQLite database to the Internet.
3. Transfer this project folder to the VPS. Copy `.env.example` to `.env`, replace *every* placeholder. Generate secret with `python3 -c "import secrets;print(secrets.token_urlsafe(64))"` and use a real SMTP provider authorized to send email. Put a real verified moderator address in MODERATOR_EMAILS.
4. Set `SITE_DOMAIN` and matching `PUBLIC_BASE_URL` (e.g., https://studymate.example.com). Do not use a trailing slash. `SESSION_SECRET` must stay unchanged across restarts.
5. Run `mkdir -p private-data && chmod 700 private-data && chmod 600 .env`, then `docker compose up -d --build`.
6. Visit `https://your-domain/`, register an AITU university email, and check delivery of the 6-digit code. Caddy requests and renews HTTPS certificates automatically when DNS and ports are configured.
7. After moderator registers and verifies their email, run `docker compose restart app` to assign the allowlisted role, then sign in again.
8. Test R1–R14 on the live URL, especially permission boundaries, registration, reports, profile save/reload, and HTTPS padlock.
9. Monitor uptime using an external service pointed to `https://your-domain/api/health` for a meaningful measurement period. Back up `private-data/studymate.sqlite` with SQLite's backup API; verify restore. A 99% figure is *not proven* merely by adding a monitor.
10. Do not expose the seeded demo database if you have real users. Sample records are synthetic; do not use real personal data in public demos.

**Limitations:** A VPS, domain/DNS and working SMTP credentials must be provided by the project team. The project still uses self-managed Python/SQLite authentication, not the Firebase Auth service proposed at Stage 1. Do not claim otherwise. SQLite and the Python standard-library server are appropriate for a coursework prototype, not a demonstrated 10,000-user production service. Re-test security before real-user public launch.

**Local development:** Run `python init_db.py` only if the database is absent, then `python server.py` or `start_windows.bat`. No SMTP is required locally: the one-time verification code appears in the server console. Production refuses to start without SMTP, HTTPS base URL and moderator allowlist.
