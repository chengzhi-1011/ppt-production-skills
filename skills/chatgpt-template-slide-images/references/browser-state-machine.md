# Browser state machine

Use `agent-browser` only with the dedicated visible profile supplied by `PPTFLOW_CHATGPT_PROFILE`. This reference describes browser behavior; `pptflow` remains the run-state authority.

## Command prefix

Use a stable session name derived from the run ID. Pass the profile and visible-mode flags on every command:

```powershell
$profile = $env:PPTFLOW_CHATGPT_PROFILE
if ([string]::IsNullOrWhiteSpace($profile)) {
    $profile = [Environment]::GetEnvironmentVariable('PPTFLOW_CHATGPT_PROFILE', 'User')
}
$session = "pptflow-$runId"
agent-browser --profile "$profile" --headed --session "$session" <command>
```

Do not add `--restore`, `--state`, `--auto-connect`, `auth`, `cookies`, or `storage`. Never point the profile at a normal Chrome or Edge user-data directory.

Because profile-backed sessions cannot use agent-browser's startup-domain containment, enforce navigation locally: after open, click, tab change, or other possible navigation, run `get url`, parse it, and continue only when scheme is `https` and host is exactly `chatgpt.com`.

## States

### 1. Preflight

- Run `pptflow status <run>` and `scripts/browser-doctor.ps1`.
- Confirm the outline and web prompt pack are current.
- Resolve the explicit external-transmission gate before upload or send.

No browser login check is part of the doctor script.

### 2. Open

Open only `https://chatgpt.com/`, then read the URL and take a fresh interactive snapshot.

- If the host differs, stop.
- If a login, authentication, password, OTP, CAPTCHA, or security challenge is visible, stop for user handoff.
- Do not inspect or manipulate stored authentication state.

### 3. Conversation

Use one dedicated conversation per run. Record its URL through the supported `pptflow ingest-image` metadata when available. For a continuation conversation, reapply the complete style lock and original template references.

Do not rename, share, publish, or delete conversations.

### 4. Upload

Take a fresh snapshot, locate the semantic file-upload control, and upload only current-page staged files. After upload or a modal change, discard every old ref and snapshot again.

The transmission gate must already cover these exact files. If the site requests an unexpected browser permission or additional file, stop.

### 5. Send

Locate the current composer semantically, read the prompt explicitly as UTF-8 (`Get-Content -Raw -Encoding UTF8 <prompt-path>` on Windows PowerShell), fill it with the exact prompt-pack text, and submit once. Do not use `agent-browser chat`; it is an unrelated AI command and is not the ChatGPT webpage composer.

Immediately re-snapshot and verify that the page contains the request marker or matching user message. If send state is uncertain, inspect before considering a resend.

### 6. Generate

Wait for the matching response, using fresh snapshots after dynamic changes. Consider generation complete only when the in-progress/stop state has ended and the matching response exposes a generated image plus a usable download action.

Poll in bounded intervals no longer than 30 seconds and keep the user updated at least once per minute. Default total generation wait is six minutes. A longer wait is allowed only when the page visibly remains active; never start another slide concurrently.

### 7. Download

Re-snapshot immediately before download and use the new download ref:

```powershell
agent-browser --profile "$profile" --headed --session "$session" download <fresh-ref> <absolute-candidate-path>
```

Verify the resulting file locally. Do not infer success from the click alone. If no download action is available, reopen the generated image once and re-snapshot. After that, pause for manual download; do not scrape blob URLs or use a screenshot substitute.

### 8. Review and handoff

Review the candidate against the approved brief and original template. Either generate the single allowed corrective request through `pptflow prompts --stage images --slide <id> --attempt 2 --correction <defect>` or register the accepted candidate with `pptflow ingest-image`. The CLI preserves the initial and corrective prompt hashes and enforces the recorded retry allowance. Verify the canonical generation log and status afterward.

### 9. Close or resume

After the batch, close only the run-scoped agent-browser session. Do not clear the dedicated profile. On resume, start with `pptflow status <run>`, reopen the recorded conversation URL on `chatgpt.com`, and inspect the last visible request/response before sending anything.

## Hard stops

- Login or CAPTCHA is required.
- URL leaves `https://chatgpt.com/`.
- The current conversation cannot be matched to the run.
- Prompt send state or download ownership remains ambiguous.
- Rate limit, policy refusal, or account restriction appears.
- Outline, template, style-lock, or prompt hash changed.
- Retry limit is exhausted.

Never bypass these stops by switching accounts, using an undocumented endpoint, or copying browser state.
