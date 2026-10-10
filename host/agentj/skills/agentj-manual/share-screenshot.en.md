<!-- generated from https://agentj.app/docs/share-screenshot/en.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# Share screenshots with Agent J

Share a screenshot to put it in the composer. Add a note if you want, then send it to the Agent on your computer. Shared text and links are never sent automatically either.

## Android {#android}

1. Open https://m.agentj.app in current Chrome. Use the browser menu to add it to your Home Screen and install the app. Open the icon and pair it. A browser that creates only a bookmark cannot register a system share target.
2. Take a screenshot, tap the system share button, and choose Agent J. You can also share several images from Photos.
3. Check the attachment tray, add an optional note, then send. If this browser is not paired, pair first and share again.

（Screenshot：Android share received in the attachment tray, local Chromium acceptance screenshot — https://agentj.app/docs/share-screenshot/android.en.png）
The share limit is 10 images and 25 MiB total. PNG, JPEG, WebP, GIF, HEIC and HEIF use the existing attachment path. Convert other image formats first. Intake stays on the phone; if it is not collected within five minutes or the system closes the page, share again. When offline, reconnect your computer; there is no ordinary upload fallback.

> **Tip:** if Agent J is missing from the share menu, open its Home Screen icon and update the page. An older installation may need reinstalling. Make sure the browser or installed app that opens has its own pairing.

## iPhone {#iphone}

The Shortcut copies the first image to this iPhone's clipboard and opens the Agent J webpage. These steps use Safari as an example; if a different browser opens, pair in that browser. It does not upload, read messages, or need a token. Safari and the Home Screen app keep separate pairings: pair once in the Safari window the Shortcut actually opens.

### Set up the Shortcut {#install-shortcut}

Download the [Shortcut source](/shortcuts/Agent-J-Share-Screenshot.unsigned.shortcut). **It is unsigned, not a one-tap installer.** A signed file for import and a one-tap iCloud installation link are coming soon; neither has been published yet. Once the signed file is available, download it in Safari to Files, open it and follow the Shortcuts import prompts. For now, build the same four-action Shortcut on your iPhone:

1. Create a Shortcut named Agent J · Share Screenshot. Enable its share-sheet option in Details and accept images only.
2. Add these actions in order: Get Item from List (Shortcut Input, First Item) → Copy to Clipboard (Local Only) → URL (`https://m.agentj.app/?from=share`) → Open URLs. Check that these are the only four actions.
3. After taking a screenshot, run it from the share sheet. Tap "Paste screenshot" in the browser that opens, complete any system paste prompt, add an optional note and send. You can also touch and hold the input field to paste.

（Screenshot：Paste button after an iPhone Shortcut launch, simulated phone view in Chromium — https://agentj.app/docs/share-screenshot/iphone.en.png）
### Clipboard permission {#clipboard}

The page reads images only after a button tap. WebKit documents a system paste menu for clipboard data from another app, which can require a second tap. If access is denied, no image is available, or clipboard reading is unsupported, the page gives a hint. Touch and hold the input field to paste, or select the image using the photo button.

**Acceptance scope:** Chromium covers the button, permitted/denied/empty clipboard and encrypted attachment path. The actual iOS Safari permission prompt and Shortcut import still need an iPhone test. These pictures show local test pages, not an iPhone system share sheet.

## Privacy {#privacy}

Android intake is handled inside the phone's browser; the screenshot is not submitted as an ordinary form upload to our website. The Shortcut URL contains only an entry marker, never image data, text or credentials. Sending uses the existing end to end encrypted attachment path. Read the [web security boundaries](https://agentj.app/security/). A copied screenshot remains on the system clipboard until you replace it.

References: [Chrome Web Share Target](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target), [WebKit clipboard API](https://webkit.org/blog/10855/async-clipboard-api/), [Apple Shortcut signing](https://support.apple.com/en-au/guide/shortcuts-mac/apd455c82f02/mac).
