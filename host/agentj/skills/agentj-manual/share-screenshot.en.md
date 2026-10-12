<!-- generated from https://agentj.app/docs/share-screenshot/en.md by agentjarvis/tools/manual_skill.mjs — do not edit -->
# Share screenshots and files with Agent J

Recordings, screenshots, Word / Excel / PowerPoint files, archives — any file can go to the Agent on your computer. It lands in the attachment tray under the composer; add a note if you like ("summarise this recording"), then send. Sharing never sends anything by itself.

## What you can send {#limits}

- Any file type, up to 10 at once and 200 MiB each — enough for an hour of phone audio.
- Audio and video are transcribed locally on your computer: the Agent gets the transcript and the path of the original. Long recordings are transcribed in 10-minute segments, up to 2 hours; if transcription fails the original still arrives and the Agent sees why. A video without sound is just a file.
- Files travel only through the end to end encrypted attachment path, at about 1 MB per second; 200 MiB takes about 4 minutes.
- While the computer runs an older Agent J, the limit is still 25 MiB and only common formats; update it to lift that.

The simplest route: in any app, save to Files first, then tap the "File" button next to the composer in the Agent J web app and pick it. The two routes below save a few taps.

## Android {#android}

1. Open https://m.agentj.app in current Chrome. Use the browser menu to add it to your Home Screen and install the app. Open the icon and pair it. A browser that creates only a bookmark cannot register a system share target.
2. In a recorder, Photos, a file manager or any app, tap the system share button and choose Agent J. You can share several files at once.
3. Check the attachment tray, add an optional note, then send. If this browser is not paired, pair first and share again.

（Screenshot：Android share received in the attachment tray, local Chromium acceptance screenshot — https://agentj.app/docs/share-screenshot/android.en.png）
Intake stays on the phone; if it is not collected within five minutes or the system closes the page, share again. When offline, reconnect your computer; there is no ordinary upload fallback.

> **Tip:** if Agent J is missing from the share menu, or only takes images, open its Home Screen icon and update the page. An older installation may need reinstalling. Make sure the browser or installed app that opens has its own pairing.

## iPhone {#iphone}

A web page on iPhone cannot receive system shares, so a Shortcut hands the file over. There are two; you can install both:

- **Send to Agent J** (recommended): takes any file — Voice Memos, recordings, screenshots, documents, text. It saves the file to the `Shortcuts/Agent J` folder in the Files app and opens the Agent J web app; tap "Choose the shared file" and pick it under Recents.
- **Agent J · Share Screenshot**: images only; copies the image to the clipboard, opens the web app, and you tap "Paste screenshot".

Neither uploads anything, reads messages, or needs a token. These steps use Safari as an example; if a different browser opens, pair in that browser. Safari and the Home Screen app keep separate pairings.

### Set up "Send to Agent J" {#send-file}

Download the [Shortcut source](/shortcuts/Agent-J-Send-File.unsigned.shortcut). **It is unsigned, not a one-tap installer**; a signed file and an iCloud installation link have not been published yet. For now, build it on your iPhone:

1. In Shortcuts, create a Shortcut named Send to Agent J. In Details, turn on Show in Share Sheet and accept Any input.
2. Add three actions in order: Save File (input Shortcut Input, Ask Where to Save off, subpath `Agent J/`, Overwrite If File Exists on) → URL (`https://m.agentj.app/?from=share&via=file`) → Open URLs. Check that these are the only three actions. Save File needs iCloud Drive; without it, turn Ask Where to Save on and pick On My iPhone each time.
3. In Voice Memos, Photos or any app, tap Share and choose Send to Agent J. In the page that opens, tap "Choose the shared file", pick the file you just saved under Recents, add a note and send.

（Screenshot：The "Choose the shared file" button after the Shortcut opens the page, simulated phone view in Chromium — https://agentj.app/docs/share-screenshot/iphone-file.en.png）
### Set up the screenshot Shortcut {#install-shortcut}

The [screenshot Shortcut source](/shortcuts/Agent-J-Share-Screenshot.unsigned.shortcut) is unsigned too. Build it by hand:

1. Create a Shortcut named Agent J · Share Screenshot. Enable its share-sheet option in Details and accept images only.
2. Add these actions in order: Get Item from List (Shortcut Input, First Item) → Copy to Clipboard (Local Only) → URL (`https://m.agentj.app/?from=share`) → Open URLs. Check that these are the only four actions.
3. After taking a screenshot, run it from the share sheet. Tap "Paste screenshot" in the browser that opens, complete any system paste prompt, add an optional note and send. You can also touch and hold the input field to paste.

（Screenshot：Paste button after an iPhone Shortcut launch, simulated phone view in Chromium — https://agentj.app/docs/share-screenshot/iphone.en.png）
The clipboard suits images only: large files on the clipboard are slow and unreliable, so recordings and other files always go through Send to Agent J.

### Clipboard permission {#clipboard}

The page reads images only after a button tap. WebKit documents a system paste menu for clipboard data from another app, which can require a second tap. If access is denied, no image is available, or clipboard reading is unsupported, the page gives a hint. Touch and hold the input field to paste, or select the image using the photo button.

**Acceptance scope:** Chromium covers the File button taking any file, "Choose the shared file" opening the picker, the paste button and the encrypted attachment path. The iPhone share sheet, the Shortcut's Save File action and the system file picker still need an iPhone test. These pictures show local test pages, not an iPhone system share sheet.

## Files through Telegram {#telegram}

A Telegram bot can download at most 20 MB per file (a Telegram limit); a recording of 20-odd minutes is often over it. The bot then tells you exactly that; use the web app's "File" button or the Send to Agent J Shortcut above instead.

## Privacy {#privacy}

Android intake is handled inside the phone's browser; files are not submitted as an ordinary form upload to our website. The Shortcut URL contains only an entry marker, never file data, text or credentials; the file stays in your own Files app until you pick it. Sending uses the existing end to end encrypted attachment path. Read the [web security boundaries](https://agentj.app/security/). A copied screenshot remains on the system clipboard, and saved files stay in the `Agent J` folder until you delete them.

References: [Chrome Web Share Target](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target), [WebKit clipboard API](https://webkit.org/blog/10855/async-clipboard-api/), [Apple Shortcut signing](https://support.apple.com/en-au/guide/shortcuts-mac/apd455c82f02/mac).
