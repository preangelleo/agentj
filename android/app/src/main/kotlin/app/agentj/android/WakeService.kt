package app.agentj.android

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioRecordingConfiguration
import android.media.MediaRecorder
import android.media.ToneGenerator
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
import app.agentj.android.wake.Level
import app.agentj.android.wake.ListenLoop
import app.agentj.android.wake.SileroVad
import app.agentj.android.wake.Wav
import app.agentj.android.wake.AudioSpec
import java.io.File

/**
 * Foreground service (type microphone) that listens for "配置的唤醒词" and records one take
 * until ~1.5 s of silence. The take is handed to MainActivity, which fills the relay page's
 * text field. Nothing is ever sent from here.
 *
 * Audio source: MediaRecorder.AudioSource.VOICE_RECOGNITION with no preferred device, so the
 * system's routing decides which microphone is used.
 *
 * It also carries the reply notifications (ReplyWatcher, ADR-050). Those do not need the
 * microphone: when listening is stopped (by the owner, or refused by the system) and they are on, the
 * service stays up as a quiet remoteMessaging foreground service — its notification is the
 * 「已停止监听 · 开始监听」 one — instead of going away.
 */
class WakeService : Service(), ListenLoop.Listener {

    companion object {
        private const val TAG = "JarvisWake"
        const val ACTION_START = "app.agentj.android.START"
        const val ACTION_STOP = "app.agentj.android.STOP"          // stop listening (user)
        const val ACTION_CAPTURE = "app.agentj.android.CAPTURE"    // record now, no wake word
        const val ACTION_END = "app.agentj.android.END"            // manual stop: keep the take
        const val ACTION_CANCEL = "app.agentj.android.CANCEL"      // drop the take
        const val ACTION_REPLIES = "app.agentj.android.REPLIES"    // reply notifications on / off changed
        /** Debug builds only: feed a 16 kHz mono WAV from the app's files dir instead of the mic. */
        const val EXTRA_DEBUG_FEED = "debug_feed"
        /** Debug builds only: wait this long before feeding (time to press Home / lock the phone). */
        const val EXTRA_DEBUG_DELAY = "debug_delay_ms"

        private const val ID_LISTEN = 1
        const val ID_CAPTURE = 2
        const val ID_READY = 4
        private const val READ = 640                                 // 40 ms
        /** MediaRecorder.AudioSource.HOTWORD (hidden): an always-on assistant's detector. */
        private const val HOTWORD = 1999

        fun start(ctx: Context) {
            // No microphone permission: starting would end in a crash (a foreground service that
            // never calls startForeground kills the process), so do not start at all.
            if (ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                Hub.set(Hub.Listen.ERROR, "没有麦克风权限"); return
            }
            try {
                ctx.startForegroundService(Intent(ctx, WakeService::class.java).setAction(ACTION_START))
            } catch (e: Exception) {
                Log.w(TAG, "start refused: $e")
                Hub.set(Hub.Listen.ERROR, "系统不让后台开麦克风：打开一次 App 即可")
                if (!MainActivity.visible) Notices.resume(ctx, "系统不让后台开麦克风，点一下恢复「配置的唤醒词」")
            }
        }

        /** Up in any mode (listening, or only watching for replies). */
        @Volatile var alive = false; private set

        /** Reply notifications (ADR-050): keep the service's watcher in step with the setting —
         *  start the service without the microphone if it is not up and they are on. */
        fun replies(ctx: Context) {
            if (Prefs.relayUrl == null) return
            if (alive) { command(ctx, ACTION_REPLIES); return }
            if (!Prefs.replyNotify) return
            try {
                ctx.startForegroundService(Intent(ctx, WakeService::class.java).setAction(ACTION_REPLIES))
            } catch (e: Exception) { Log.w(TAG, "replies: start refused: $e") }
        }

        fun command(ctx: Context, action: String) {
            try { ctx.startService(Intent(ctx, WakeService::class.java).setAction(action)) } catch (_: Exception) {}
        }
    }

    private val main = Handler(Looper.getMainLooper())
    @Volatile private var running = false
    @Volatile private var debugFeed: ShortArray? = null
    private var thread: Thread? = null
    @Volatile private var loop: ListenLoop? = null
    /** Audio session of our own AudioRecord (0 = none), to tell our recording from anyone else's. */
    @Volatile private var ourSession = 0
    private var others = emptySet<Int>()
    private var ownSilenced = false
    private val yieldTo = mutableSetOf<Int>()
    private var replies: ReplyWatcher? = null
    private val recordings = object : AudioManager.AudioRecordingCallback() {
        override fun onRecordingConfigChanged(configs: List<AudioRecordingConfiguration>) = othersChanged(configs)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        alive = true
        val am = getSystemService(AudioManager::class.java)
        others = am.activeRecordingConfigurations.map { it.clientAudioSessionId }.toSet()
        am.registerAudioRecordingCallback(recordings, main)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                Log.i(TAG, "stopped by the user")
                Prefs.stoppedByUser = true
                shutdown()
                if (Prefs.replyNotify && quietForeground()) { syncReplies(); return START_STICKY }
                stopForeground(STOP_FOREGROUND_REMOVE)
                Notices.stopped(this)
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_REPLIES -> {
                if (thread != null) { syncReplies(); return START_STICKY }      // listening: the type already covers it
                if (!Prefs.replyNotify || !quietForeground()) {
                    replies?.stop(); replies = null
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    if (Prefs.stoppedByUser) Notices.stopped(this)
                    stopSelf()
                    return START_NOT_STICKY
                }
                syncReplies()
                return START_STICKY
            }
            ACTION_START -> Prefs.stoppedByUser = false
            ACTION_END -> { loop?.stop(); return START_STICKY }
            ACTION_CANCEL -> { loop?.cancel(); return START_STICKY }
            ACTION_CAPTURE -> { ReplySpeech.stop(); if(loop!=null)loop?.startCapture() else pendingCapture=true }
        }
        if (!goForeground()) {
            // No microphone (refused from the background): the replies can still be watched.
            if (Prefs.replyNotify && quietForeground()) { syncReplies(); return START_STICKY }
            stopSelf(); return START_NOT_STICKY
        }
        syncReplies()
        if (BuildConfig.DEBUG) intent?.getStringExtra(EXTRA_DEBUG_FEED)?.let { name ->
            val f = File(filesDir, File(name).name)
            if (f.isFile) {
                val pcm = Wav.decode(f.readBytes())
                val wait = intent.getLongExtra(EXTRA_DEBUG_DELAY, 0L).coerceIn(0L, 60_000L)
                if (wait > 0) main.postDelayed({ debugFeed = pcm }, wait) else debugFeed = pcm
            }
        }
        if (thread == null) startAudio()
        return START_STICKY
    }

    /** Swiped away in recents while listening: ColorOS kills the process next (see ReviveReceiver). */
    override fun onTaskRemoved(rootIntent: Intent?) {
        if ((running && !Prefs.stoppedByUser) || replies != null) ReviveReceiver.schedule(this)
        super.onTaskRemoved(rootIntent)
    }

    override fun onDestroy() {
        getSystemService(AudioManager::class.java).unregisterAudioRecordingCallback(recordings)
        main.removeCallbacks(unyield)
        Hub.yielding = false
        shutdown()
        replies?.stop(); replies = null
        alive = false
        super.onDestroy()
    }

    private fun shutdown() {
        running = false
        thread?.interrupt()
        thread = null
        cancelCaptureNotice()
        Hub.level = 0f
        Hub.set(Hub.Listen.OFF)
    }

    // ---- foreground ------------------------------------------------------------------

    private fun goForeground(): Boolean {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Hub.set(Hub.Listen.ERROR, "没有麦克风权限")
            return false
        }
        return try {
            startForeground(ID_LISTEN, listenNotice(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                if (Prefs.replyNotify) ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING else 0)
            // Listening again: the "Agent J 没在听" / 已停止 notification is stale now.
            Notices.clear(this)
            true
        } catch (e: Exception) {
            // Android 14+: a microphone service cannot start while the app is in the background
            // (e.g. restarted by the system after being killed). One tap on the app fixes it.
            Log.w(TAG, "startForeground refused: $e")
            Hub.set(Hub.Listen.ERROR, "系统不让后台开麦克风：打开一次 App 即可")
            Notices.resume(this, "系统停掉了监听，点一下恢复「配置的唤醒词」")
            false
        }
    }

    /** Foreground without the microphone, for the reply watcher alone. Its notification is the
     *  quiet 「已停止监听」 one (same channel, same 开始监听 button), so it replaces that. */
    private fun quietForeground(): Boolean {
        val stopped = Prefs.stoppedByUser
        val n = Notification.Builder(this, JarvisApp.CH_STOPPED)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle(if (stopped) "Agent J 已停止监听" else "Agent J 没在听")
            .setContentText(if (stopped) "点「开始监听」恢复「配置的唤醒词」；你的 Agent的新回复照常提醒"
                            else "点一下恢复「配置的唤醒词」；你的 Agent的新回复照常提醒")
            .setContentIntent(openApp(14, false))
            .addAction(Notification.Action.Builder(null, "开始监听", PendingIntent.getForegroundService(this, 15,
                Intent(this, WakeService::class.java).setAction(ACTION_START),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)).build())
            .setOngoing(true)
            .setGroup("jarvis.resume")
            .build()
        return try {
            startForeground(ID_LISTEN, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING)
            if (stopped) Notices.clear(this)
            Log.i(TAG, "foreground for replies only (stopped by the user: $stopped)")
            true
        } catch (e: Exception) {
            Log.w(TAG, "replies-only foreground refused: $e"); false
        }
    }

    /** The reply watcher runs whenever the service is up and the setting is on. */
    private fun syncReplies() {
        if (Prefs.replyNotify && Prefs.relayUrl != null) {
            if (replies == null) replies = ReplyWatcher(applicationContext) { recordingNow() }.also { it.start() }
            else replies?.kick()
        } else { replies?.stop(); replies = null }
    }

    /**
     * A reply's chime would land in a recording: our wake take, the page's hold-to-talk, or another
     * app recording (voice typing, a voice note) — not our own idle listener, not an always-on
     * hotword, not a recorder the system has silenced. A call counts too.
     */
    private fun recordingNow(): Boolean {
        if (Hub.state == Hub.Listen.CAPTURING || Hub.pausedForPage) return true
        val am = getSystemService(AudioManager::class.java)
        if (am.mode == AudioManager.MODE_IN_CALL || am.mode == AudioManager.MODE_IN_COMMUNICATION) return true
        val own = ourSession
        return am.activeRecordingConfigurations.any {
            (own == 0 || it.clientAudioSessionId != own) && !it.isClientSilenced &&
                it.clientAudioSource != HOTWORD
        }
    }

    private fun pending(action: String, code: Int): PendingIntent =
        PendingIntent.getService(this, code, Intent(this, WakeService::class.java).setAction(action),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun openApp(code: Int, fromWake: Boolean): PendingIntent = Popup.pending(this, code, fromWake)

    private fun listenNotice(): Notification =
        Notification.Builder(this, JarvisApp.CH_LISTEN)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Agent J 在听")
            .setContentText("说「配置的唤醒词」开始；说完的话只填进输入框，不会自动发送")
            .setContentIntent(openApp(10, false))
            .addAction(Notification.Action.Builder(null, "说话", pending(ACTION_CAPTURE, 11)).build())
            .addAction(Notification.Action.Builder(null, "停止监听", pending(ACTION_STOP, 12)).build())
            .addAction(Notification.Action.Builder(null, "状态", PendingIntent.getActivity(this, 13,
                Intent(this, StatusActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)).build())
            .setOngoing(true)
            .setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
            .build()

    /**
     * High-priority notification while a take is recording. Its full-screen intent is the
     * compliant way to bring the app forward when the screen is off or locked; on a phone in
     * use it is a heads-up (see Popup for the direct path). Silent on purpose: a notification
     * sound here would be recorded into the take — the wake chime has already played.
     */
    private fun showCaptureNotice() {
        val n = Notification.Builder(this, JarvisApp.CH_WAKE)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Agent J 正在听你说…")
            .setContentText("停顿约 1.5 秒自动结束；文字会填进输入框，不会自动发送")
            .setCategory(Notification.CATEGORY_CALL)
            .setContentIntent(openApp(20, true))
            .setFullScreenIntent(openApp(21, true), true)
            .setGroup("jarvis.capture")
            .addAction(Notification.Action.Builder(null, "说完了", pending(ACTION_END, 22)).build())
            .addAction(Notification.Action.Builder(null, "取消", pending(ACTION_CANCEL, 23)).build())
            .setOngoing(true)
            .setAutoCancel(false)
            .build()
        try { getSystemService(NotificationManager::class.java).notify(ID_CAPTURE, n) } catch (_: SecurityException) {}
    }

    /**
     * The take is waiting and the app did not come forward: a heads-up with sound (recording is
     * over, so now it may ring). One tap opens the app and the words go into the field; on a
     * locked phone its full-screen intent lights the screen and asks for the unlock.
     */
    private fun readyNotice() {
        val n = Notification.Builder(this, JarvisApp.CH_READY)
            .setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentTitle("Agent J：说完了，点这里")
            .setContentText("文字会填进输入框，检查后再自己发送")
            .setCategory(Notification.CATEGORY_MESSAGE)
            .setContentIntent(openApp(24, true))
            .setFullScreenIntent(openApp(25, true), true)
            // Its own group: Android 15+ auto-groups an app's loose notifications and re-posts
            // the children silently, which took the sound (and the heads-up) away in 1.1 tests.
            .setGroup("jarvis.ready")
            .setAutoCancel(true)
            .build()
        try { getSystemService(NotificationManager::class.java).notify(ID_READY, n) } catch (_: SecurityException) {}
    }

    private fun cancelCaptureNotice() {
        getSystemService(NotificationManager::class.java).cancel(ID_CAPTURE)
    }

    // ---- audio -----------------------------------------------------------------------

    private fun asset(name: String): ByteArray = assets.open("models/$name").use { it.readBytes() }

    @Volatile private var pendingCapture=false
    private fun startAudio() {
        running = true
        Hub.set(Hub.Listen.STARTING)
        thread = Thread({ audioMain() }, "jarvis-audio").apply { priority = Thread.MAX_PRIORITY; start() }
    }

    private fun audioMain() {
        var rec: AudioRecord? = null
        try {
            KwsDetector(assets).use { engine ->
            SherpaVad(assets).use { vad ->
                val l = ListenLoop(engine, vad, this, Prefs.threshold)
                loop = l
                val buf = ShortArray(READ)
                while (running) {
                    if(pendingCapture){pendingCapture=false;l.startCapture()}
                    l.threshold = Prefs.threshold
                    debugFeed?.let { pcm ->
                        rec?.release(); rec = null
                        feedPaced(l, pcm)
                        debugFeed = null
                    }
                    // The keyboard never ends a wake take: showing up during one (the page's field grabbing
                    // focus as the app comes forward) cancelled the take in 1.3.0; MainActivity hides it.
                    val kb = Hub.keyboardUp && l.mode != ListenLoop.Mode.CAPTURING
                    if (Hub.pausedForPage || Hub.yielding || kb || (!Prefs.wakeEnabled && l.mode != ListenLoop.Mode.CAPTURING)) {
                        rec?.release(); rec = null; ourSession = 0; Hub.level = 0f
                        if (l.mode == ListenLoop.Mode.CAPTURING) { l.cancel(); l.feed(buf, 0) }
                        Hub.set(if(!Prefs.wakeEnabled)Hub.Listen.OFF else if (Hub.pausedForPage) Hub.Listen.PAUSED else Hub.Listen.YIELDED)
                        Thread.sleep(50); continue
                    }
                    if (rec == null) {
                        rec = openRecorder()
                        if (rec == null) {
                            Hub.set(Hub.Listen.ERROR, "打不开麦克风（被别的 App 占用？）")
                            Thread.sleep(1000); continue
                        }
                    }
                    val n = rec!!.read(buf, 0, buf.size)
                    if (n > 0) l.feed(buf, n)
                    else if (n < 0) { rec?.release(); rec = null; ourSession = 0; Thread.sleep(200); continue }
                    Hub.level = if (n > 0 && l.mode == ListenLoop.Mode.CAPTURING) Level.of(buf, n) else 0f
                    // Not after a stop: shutdown() has already said OFF.
                    if (running) Hub.set(if (l.mode == ListenLoop.Mode.CAPTURING) Hub.Listen.CAPTURING else Hub.Listen.LISTENING)
                }
            } }
        } catch (_: InterruptedException) {
        } catch (t: Throwable) {
            Log.e(TAG, "audio thread died", t)
            Hub.set(Hub.Listen.ERROR, "监听出错：${t.javaClass.simpleName}")
        } finally {
            try { rec?.release() } catch (_: Exception) {}
            ourSession = 0
            loop = null
        }
    }

    /** Debug feed: real-time pacing so the activity sees the same states as with a real mic. */
    private fun feedPaced(l: ListenLoop, pcm: ShortArray) {
        val all = pcm + ShortArray(16_000 * 3)
        var i = 0
        while (i < all.size && running) {
            val n = minOf(READ, all.size - i)
            val chunk = all.copyOfRange(i, i + n)
            l.feed(chunk)
            Hub.level = if (l.mode == ListenLoop.Mode.CAPTURING) Level.of(chunk) else 0f
            Hub.set(if (l.mode == ListenLoop.Mode.CAPTURING) Hub.Listen.CAPTURING else Hub.Listen.LISTENING)
            i += n
            Thread.sleep(40)
        }
    }

    private fun openRecorder(): AudioRecord? {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return null
        return try {
            val rate = AudioSpec.SAMPLE_RATE
            val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val r = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, rate,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, rate * 2))
            if (r.state != AudioRecord.STATE_INITIALIZED) { r.release(); return null }
            ourSession = r.audioSessionId
            r.startRecording()
            r
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord: $e"); null
        }
    }

    // ---- sharing the microphone with the keyboard ------------------------------------

    /**
     * Someone else started recording. Android gives the microphone to the app on screen, so
     * while Agent J is visible its own wake listener wins and the keyboard's voice typing (Gboard)
     * only gets silence and gives up. (It gives up even when we let go 30 ms later, so for the
     * keyboard MainActivity releases the microphone as soon as the keyboard shows — Hub.keyboardUp;
     * this callback covers every other recorder.) A recording that starts while Agent J is on screen gets
     * the microphone; the listener takes it back ~1.2 s after that recording ends. Recorders
     * that were already running (an always-on hotword, say) do not make it yield — they coexist.
     */
    private fun othersChanged(configs: List<AudioRecordingConfiguration>) {
        val own = ourSession
        val mine = configs.firstOrNull { it.clientAudioSessionId == own }
        if (mine != null && mine.isClientSilenced != ownSilenced) {
            ownSilenced = mine.isClientSilenced
            Log.i(TAG, if (ownSilenced) "our microphone is silenced by the system" else "our microphone hears again")
        }
        val now = configs.filter { own == 0 || it.clientAudioSessionId != own }
        val ids = now.map { it.clientAudioSessionId }.toSet()
        if (MainActivity.visible) yieldTo += ids - others
        others = ids
        yieldTo.retainAll(ids)
        if (yieldTo.isNotEmpty()) {
            main.removeCallbacks(unyield)
            if (!Hub.yielding) Log.i(TAG, "another app is recording (${yieldTo.size}): giving it the microphone")
            Hub.yielding = true
        } else if (Hub.yielding) {
            main.removeCallbacks(unyield)
            main.postDelayed(unyield, 1200)
        }
    }

    private val unyield = Runnable {
        if (yieldTo.isEmpty() && Hub.yielding) { Log.i(TAG, "microphone free again: listening"); Hub.yielding = false }
    }

    // ---- ListenLoop.Listener (audio thread) -------------------------------------------

    override fun onWake(score: Float) {
        ReplySpeech.stop()
        Log.i(TAG, "wake score=$score")
        Prefs.lastWake = System.currentTimeMillis()
        Prefs.lastWakeScore = score
        Hub.set(Hub.Listen.CAPTURING)
        main.post {
            chime()
            // On screen already: the orb in the middle is the indicator (with 说完了 / 取消); the
            // heads-up would only drop a second, white bar over the top of it.
            if (!MainActivity.visible) showCaptureNotice()
            Popup.bringForward(this, ListenOrb.Phase.LISTENING)
        }
    }

    override fun onTake(wav: ByteArray, seconds: Double, reason: String) {
        Log.i(TAG, "take ${"%.1f".format(seconds)} s ($reason)")
        Hub.postTake(Hub.Take(wav, seconds))
        main.post {
            cancelCaptureNotice()
            if (!MainActivity.visible) readyNotice()
            Popup.bringForward(this, ListenOrb.Phase.RECOGNIZING)
        }
    }

    override fun onEmpty(reason: String) {
        Log.i(TAG, "empty take ($reason)")
        main.post { cancelCaptureNotice() }
        Hub.say(if (reason == "CANCEL") "已取消" else "没听到说话")
    }

    private fun chime() {
        try {
            val t = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 80)
            t.startTone(ToneGenerator.TONE_PROP_ACK, 180)
            main.postDelayed({ t.release() }, 600)
        } catch (_: Exception) {}
        try {
            getSystemService(Vibrator::class.java)?.vibrate(VibrationEffect.createOneShot(40, VibrationEffect.DEFAULT_AMPLITUDE))
        } catch (_: Exception) {}
    }
}
