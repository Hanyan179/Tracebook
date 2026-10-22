# Website QA · 2026-10-22

## Scope

Six static pages (Chinese and English product, getting started and privacy). Source baseline: `4afe6e2b7c26e85ab8e7090328027f4ad609fc00`. Existing logo assets were visually inspected and reused unchanged. No client, node, data contract, deployment or account changes.

## Passed

- Standard-library build produces all six HTML pages deterministically.
- Six dependency-free unit tests: expected page count; local links, assets and fragments; external repository document destinations against the checked-out source; landmarks and image alternatives; explicit product limits; language-switch round trips; brand asset equality; no scripts/forms/iframes/event handlers; focus and reduced-motion CSS presence.
- Independent read-only content review against the brief, development and security documents found no blocking content issue. Corrected a duplicate English page-title brand during review.
- All external link destinations are the known project repository, existing public documents, or its private vulnerability reporting route. No unverified download or signup links.
- Static site has no JavaScript requirement or runtime third-party assets.

## Not completed: real browser and visual acceptance

No real website screenshots were produced. An isolated local preview process was started, but another command and the cloud browser could not connect to its localhost port (`ERR_CONNECTION_REFUSED`). Installed Chromium in the default execution sandbox failed at a process socket with `Operation not permitted`. A subsequent official elevated execution request with Chromium sandbox enabled failed before the test process started because the environment wrapper could not create a required file on a read-only filesystem. No security settings were changed and no deployment was used as a workaround.

The browser script is supplied for a compatible environment, but it has NOT passed here. Desktop/mobile rendering, visual contrast, clipping, touch hit areas, actual keyboard focus, browser back/forward behavior, reduced-motion emulation and no-JavaScript navigation still require real-browser validation. CSS and HTML inspection alone do not establish these outcomes. Screen-reader and real-device checks also remain outstanding.

Before publication, run `tests/browser_check.py`, inspect every generated screenshot, verify all intended links and the complete keyboard path, and check touch/zoom/contrast with real rendering. Preview must not be substituted by a fake app screenshot. No publication target has been authorized.

## Product limitations retained in both languages

- Source-only release, no signed/notarized installer.
- Conversation returns local retrieval, not generated model answers.
- Text formats are UTF-8 `.md`, `.markdown`, `.txt`, `.text`, up to 2 MB per file; no claim of PDF/Office/scan support.
- Confirmation exists for recording-backed candidates; unified multi-source memory and revision remain unfinished.
- Real automatic transcription and real-model quality acceptance remain unfinished.
- Queues, checks and folder exchange are not a claim of verified remote/background cloud sync.
- Portable text export excludes original audio and credentials and is not a full restore package.
- Third-party model processing depends on configuration and activation; provider fees may apply. No absolute “data never leaves your device” claim.
