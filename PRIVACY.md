# Privacy

This project is local-first, but some optional workflow stages can transmit task data to external services.

## Local data

`pptflow init` creates immutable task-local snapshots of supplied materials and templates. Run directories may also contain extracted claims, prompts, generated images, editable PPTX files and QA renders. Keep these directories outside the Git repository and do not publish them.

## ChatGPT web stage

The ChatGPT browser skill may send approved slide text and selected sanitized reference images to `https://chatgpt.com/` only after an explicit, task-scoped transmission record is created. The skill uses a dedicated visible browser profile and must not read or export passwords, cookies, local storage, OTPs or CAPTCHA results.

## PaddleOCR stage

When the user enables PaddleOCR-VL, task page images are sent to the provider for text recognition. The user must review the provider's current privacy, quota, pricing and service terms before enabling this optional backend. Offline geometric text hints remain available without a token.

## Image API stage

Image generation and editing use the official OpenAI API or a user-selected OpenAI-compatible API. The configured endpoint receives the API key plus the task-local prompts, images and masks required for that operation. A custom `OPENAI_BASE_URL` is a separate third party; review and trust that provider before configuring it. Do not embed credentials or tokens in that URL. The CLI rejects URL user information, query parameters, fragments, and non-HTTPS remote endpoints; plain HTTP is accepted only for a loopback development service.

`editppt` accepts `OPENAI_API_KEY` and `PADDLE_OCR_TOKEN` only from the process environment or a secret manager. Secret values are not accepted as command-line options, are not written to `~/.editppt/config.yaml`, and are reported only as `set` or `unset`.

## Credential locations

Credentials are not stored in this repository. Local tools may use:

- process environment variables or a user-managed secret manager for API/OCR secrets;
- `%USERPROFILE%\.editppt\config.yaml` for non-secret API base URL and model settings;
- `%USERPROFILE%\.pptflow\chatgpt-profile` for the user-managed dedicated browser profile.

Never commit these paths or copy their contents into issues, logs, prompts or support bundles.
