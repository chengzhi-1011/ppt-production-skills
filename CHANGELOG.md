# Changelog

## 0.2.0 - 2026-08-07

### Changed

- Prepared the repository for public release under Apache License 2.0.
- Removed local-login token reuse and the undocumented image backend; image operations now use documented OpenAI-compatible API configuration only.
- Replaced PyMuPDF with `pypdfium2` PDF rendering and lossless per-page image handling.
- Moved API/OCR secrets out of command-line and plaintext YAML configuration paths.
- Added public-release security, dependency, privacy and regression checks.

## 0.1.0 - 2026-08-07

### Added

- Evidence-bounded material analysis and page-by-page outline approval.
- ChatGPT template-reference image workflow with explicit transmission scope and bounded retries.
- Object-level editable PowerPoint reconstruction.
- Deterministic `pptflow` state gates and real Microsoft PowerPoint open/export QA.
- Windows installer, recoverable uninstaller, and repository verification script.
