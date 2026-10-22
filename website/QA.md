# Website QA · 2026-10-22

## Scope

Six static pages (Chinese and English product, getting started and privacy). Source baseline: `4afe6e2b7c26e85ab8e7090328027f4ad609fc00`. Existing logo assets were visually inspected and reused unchanged. No client, node or data-contract changes. The initial code-only delivery was followed by an explicitly authorized, owner-private static deployment.

## Passed

- Standard-library build produces all six HTML pages deterministically.
- Six dependency-free unit tests: expected page count; local links, assets and fragments; external repository document destinations against the checked-out source; landmarks and image alternatives; explicit product limits; language-switch round trips; brand asset equality; no scripts/forms/iframes/event handlers; focus and reduced-motion CSS presence.
- Independent read-only content review against the brief, development and security documents found no blocking content issue. Corrected a duplicate English page-title brand during review.
- All external link destinations are the known project repository, existing public documents, or its private vulnerability reporting route. No unverified download or signup links.
- Static site has no JavaScript requirement or runtime third-party assets.

## Not completed: real browser and visual acceptance

No real website screenshots were produced. An isolated local preview process was started, but another command and the cloud browser could not connect to its localhost port (`ERR_CONNECTION_REFUSED`). Installed Chromium in the default execution sandbox failed at a process socket with `Operation not permitted`. A subsequent official elevated execution request with Chromium sandbox enabled failed before the test process started because the environment wrapper could not create a required file on a read-only filesystem. No security settings were changed and no deployment was used as a workaround.

The browser script is supplied for a compatible environment, but it has NOT passed here. Desktop/mobile rendering, visual contrast, clipping, touch hit areas, actual keyboard focus, browser back/forward behavior, reduced-motion emulation and no-JavaScript navigation still require real-browser validation. CSS and HTML inspection alone do not establish these outcomes. Screen-reader and real-device checks also remain outstanding.

Before treating visual acceptance as complete, run `tests/browser_check.py`, inspect every generated screenshot, verify all intended links and the complete keyboard path, and check touch/zoom/contrast with real rendering. Preview must not be substituted by a fake app screenshot. At the initial code-only delivery, no publication target had been authorized; the later private deployment is recorded below.

## Product limitations retained in both languages

- Source-only release, no signed/notarized installer.
- Conversation returns local retrieval, not generated model answers.
- Text formats are UTF-8 `.md`, `.markdown`, `.txt`, `.text`, up to 2 MB per file; no claim of PDF/Office/scan support.
- Confirmation exists for recording-backed candidates; unified multi-source memory and revision remain unfinished.
- Real automatic transcription and real-model quality acceptance remain unfinished.
- Queues, checks and folder exchange are not a claim of verified remote/background cloud sync.
- Portable text export excludes original audio and credentials and is not a full restore package.
- Third-party model processing depends on configuration and activation; provider fees may apply. No absolute “data never leaves your device” claim.

## Authorized hosted preview

The user subsequently requested deployment. Version 1 was published successfully through the official Sites workflow at https://tracebook.rare-raven-7022.chatgpt.site from exact pushed static-source commit `f1b5ede56a833f75548d75ef149578053fc07106`. Access was verified as owner-only, with no additional users or groups. Only generated website files and the hosting manifest were packaged. No other site or GitHub main branch was changed.

The earlier local browser limitations remain historical facts. Hosted-browser findings will be recorded separately rather than retroactively claiming those local checks passed.

The independent cloud-browser visit to the live URL displayed the expected private sign-in gate ("Sign in required" / "Log in to access"), not the website. The reviewer did not change sharing or bypass authentication. Therefore hosted product rendering, responsive layout and actual website interactions are still unverified; successful deployment and the access gate are not visual acceptance. The owner can sign in to inspect the deployed version.

## Visual revision (22 October 2026)

The homepage now uses original HTML/CSS product illustrations, not running app screenshots: a three-device composition, three synthetic source entries, original/candidate/confirmed layers, a retrieval excerpt linked to its synthetic original, and file ownership artwork. English and Chinese have equivalent content. The public Auday reference was visually inspected in the cloud browser; none of its images, copy, metrics or layouts were copied. Layout details were grounded in MacOverview, MacLibraryBrowser, ConversationView, PhoneRecordingScreen and WatchRecordingView source.

These are synthetic/concept illustrations, clearly labeled on the page. Recording is not presented as automatic transcription; retrieval does not generate an answer. Manual multi-source memory/revision is available in main 4af50d5; automated model memory organization remains in development. Recording controls in the illustration are non-interactive, not false download or playback buttons.

Static checks are repeated for this revision. Rendered desktop/mobile, overflow, actual keyboard/focus, and visual contrast acceptance remain **unverified**: the live private Site requires owner login in the cloud browser. No access policy was changed to bypass this gate. The existing v1 remains available as a rollback version.
