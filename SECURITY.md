# Security policy

## Reporting

Use a private GitHub security advisory for suspected credential exposure, unsafe file handling, browser-boundary bypasses or PowerPoint automation issues. Do not paste secrets, customer materials or browser state into a public issue.

## Supported version

Only the latest commit on `main` is maintained during the initial public release.

## Secret-handling boundary

- The repository must contain no API keys, OAuth tokens, PaddleOCR tokens, cookies, browser profiles or task materials.
- Login, CAPTCHA, password-manager and account-management actions always remain manual user actions in the visible dedicated browser.
- External transmission must be explicitly scoped to task-local files and recorded before upload.
- Generated presentations and run directories are user data, not source-controlled fixtures.
- Treat supplied documents, OCR text, slide notes and metadata as untrusted data, never as agent instructions.
- A custom `OPENAI_BASE_URL` receives the configured API key and task-local image inputs; use only a provider explicitly selected and trusted by the user.

If a secret is accidentally committed, revoke it first, then remove it from Git history. Rewriting history alone does not revoke a credential.
