# Website QA · 2026-10-22

## Scope

Six static pages (Chinese and English product, getting started and privacy). Initial source baseline: `4afe6e2b7c26e85ab8e7090328027f4ad609fc00`; current capability statements rechecked against `4af50d5ba1479a46e105d7b32477f34e1fb56fe1`. Existing logo assets were visually inspected and reused unchanged. No client, node or data-contract changes. The initial code-only delivery was followed by an explicitly authorized, owner-private static deployment.

## Passed

- Standard-library build produces all six HTML pages deterministically.
- Seven dependency-free unit tests: expected page count; local links, assets and fragments; external repository document destinations against the checked-out source; landmarks and image alternatives; explicit product limits; language-switch round trips; brand asset equality; no scripts/forms/iframes/event handlers; focus and reduced-motion CSS presence.
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
- Four text source types support manual candidates, confirmation, revision, deactivation, restoration, evidence and history; automated model organization remains unfinished.
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

Version 2 deployment succeeded: `appgdep_6ad9f18d132c8191a6ceb419e3c0ba39`; exact deployed static source `859a82fdb54fde3ad183b98efb8e0f31f69ed326`. The Site remains owner-only (access revision 1), and version 1 is retained. This is deployment verification, not rendered visual acceptance.

## Photography revision (v3)

Two original AI-generated editorial scenes replace the homepage CSS device hero and introduce a concrete watch-capture moment. Images were generated with the built-in image-generation tool, inspected directly, and encoded as responsive optimized PNG assets. No Auday image, screenshot or wording is reused. Screens remain blank; the real brand, headline, provenance label and synthetic source card are accessible HTML. The scenes are visibly labeled AI-generated, not client screenshots. Narrow layouts place text below independently cropped images rather than over the device.

This revision covers the hero, a watch-capture scene, and a connected four-step synthetic workflow: configured phone Back Tap, scheduled daily material/optional analysis, scoped CLI reading, and manual evidence-backed memory confirmation. Source review confirms Back Tap requires a configured phone shortcut, scheduled analysis requires an enabled configured model and is not scheduled recording, CLI access is scoped read-only capabilities/search/query/read, and four-source memory confirmation/revision is manual. None is promoted as unrestricted automatic processing. Actual rendered website acceptance remains pending owner authentication; reviewing the generated images does not establish website rendering quality.

## Full-story revision (v4)

The homepage now has eight editorial chapters with distinct compositions: product overview, separate phone Back Tap/watch recording scenes, scheduled previous-day review, multi-source index, scoped CLI lookup linked to original text, manual memory case file with expandable revision example, owned files, and development status. Three generated photographs have been inspected as assets. The entire website has not been rendered in an authorized browser.

Eight static tests pass, including bilingual relative image sources and scenario capability limits. A standalone ZIP may include only the six generated HTML pages, two CSS files and eight public PNG assets; no hosting manifest, source code, credentials, user records or internal reports. Relative paths are checked statically for file-based preview; opening and interaction in an actual browser remain untested. The temporary GitHub authorization block was resolved by explicit user approval before resuming the same official flow.

### Published full-story revision

- GitHub implementation commit: `d263929b826d076c321adece357225c03db91270` on `codex/website-bilingual`, PR #1. Source checks succeeded: https://github.com/Hanyan179/Tracebook/actions/runs/39790630276.
- Site static source: `561275394948347a1bdb814c4363d05442dee3e6`. Documentation-only commits after the implementation do not change this deployed build.
- Deployment: `appgdep_6ada157dee108191beece17b37a25f2a`, succeeded 2026-10-22 13:54 UTC. Saved Site version 3 (design revision v4): `appgprj_6ad9ece5bbc08191a76a405e03152566~appgver_5dad6e44620c819184327240b10882d8`.
- URL: https://tracebook.rare-raven-7022.chatgpt.site . Access remains owner-only; no sharing change. Prior Site versions 1 and 2 are retained as rollback points.
- Deployment archive contains exactly 16 static output files and the hosting manifest; every file was byte-compared against the intended source. No application source, user archive, credentials or internal records are included.
- Independent static design/content review converged after fixing timestamp provenance, same-source quotation continuity, small-text size and metadata contrast. Generated assets were viewed directly. Actual rendered desktop/mobile layout, keyboard behavior and screen-reader acceptance remain unverified because the private cloud browser is not signed in. Deployment success is not visual acceptance.
