# Agent J Android review APK — 0.12 alpha

This test APK uses `app.agentj.android.debug`, independent of relay. It is not a store release. Do not uninstall or replace relay to test it.

1. Install `reports/android/agentj-0.12.0-alpha-debug.apk` on an emulator or a test device.
2. In Agent J settings enter the host-generated pairing URL at `https://m.agentj.app/` (or the alpha phone domain). Pair in the app using the host-terminal code and passphrase. There is no admin key.
3. Allow microphone and notifications. Background wake is off by default. On the host run `agentj config set voice.wake_enabled true` and choose `voice.wake_word`; empty follows the Agent name.
4. Keep the app connected once to synchronize keyword phonemes. For unfamiliar English proper names set `voice.wake_pronunciation`; unsupported pronunciation fails visibly on the host.
5. Say the phrase, then dictate. The recognized text is placed into the input. It never automatically sends. Lockscreen captures remain hidden until unlock. Manual capture still works with background wake disabled.
6. Android foreground-service, overlay, full-screen notification, battery optimization and OEM background restrictions apply. Use the status page to open system permission screens. Do not assume boot/background revival is universally permitted.

The detector is offline bilingual sherpa-onnx streaming KWS. It changes keyword tokens when the host name/preferences change; no per-user training or cloud wake recognition. ASR runs on the owner host or chosen own-key cloud provider. Threshold/accuracy/battery and real-device lockscreen/background acceptance still require measurement. The current APK loads the phone UI from the published pinned origin: it has the same operator-JavaScript trust limitation as the PWA, not a bundled native zero-access UI.

Reply watcher resumes the paired Noise session. It receives only ciphertext from the blind relay and emits generic lockscreen notifications, without reply body. Background WebView delivery can be suspended by Android. iOS PWA cannot run background microphone wake; a native iOS app with system-approved audio lifecycle is a future route.

Build requires JDK 17, Android SDK 35, and pinned models/AAR described in `MODEL_PROVENANCE.json`. Release signing/store distribution is intentionally absent. Never use relay keystores. Rebuild after source changes before simulator acceptance.
