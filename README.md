# StudyMate — Final prototype

Responsive AITU study-partner web application built with HTML, CSS, JavaScript, Python HTTP server and SQLite.

## Local run (Windows or Linux)

1. Extract the ZIP.
2. Run `start_windows.bat` on Windows, or `python init_db.py && python server.py` on Linux/macOS.
3. Open `http://127.0.0.1:8000`.
4. Register with a university-format email. In local development the six-digit code appears **in the server console**, not in real email. Check the console, then verify and sign in.
5. Use a second browser profile to test applicants/organizer. See `test_final.py` for reproducible access-control checks.

## Public HTTPS deployment

See `DEPLOY_PUBLIC.md`, `.env.example`, `Dockerfile`, `compose.yaml`, and `Caddyfile`.
**This archive does not include a deployed website or public URL.** Deployment requires an account/VPS, a configured domain and SMTP account.

## Final requirements

See `R1_R14_FINAL_STATUS.md`. Not all requirements can be proven through code alone: uptime, external HTTPS, 360 px real-phone tests, and independent user re-tests must be performed on the actual final release.

## Privacy and security

- Only authenticated users access application records. Passwords use salted scrypt hashes. CSRF token required for mutations.
- Accepted-application contact details are only visible to the two permitted participants.
- Moderator rights are controlled by the server-side `MODERATOR_EMAILS` list.
- Never commit `.env`, SQLite files containing private data, or `.local_session_secret` to GitHub.
- The project is a pilot/academic prototype. Do not represent the synthetic analytics as real campus activity.

## Verification

Run `python test_endterm.py` and `python test_final.py`. They use temporary copies of the database.

## Render hosting option (additional)
`render.yaml`, `start_render.sh` and `RENDER_PUBLIC_DEPLOY.md` prepare the project for a hosted HTTPS endpoint with persistent SQLite. They do not establish a live URL without an authenticated Render deployment. The Blueprint chooses a paid plan to avoid database loss on free ephemeral instances.
