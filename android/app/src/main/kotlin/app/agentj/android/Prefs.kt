package app.agentj.android

import android.content.Context
import android.content.SharedPreferences

/** App settings. Private to the app; allowBackup=false keeps the relay address on the phone. */
object Prefs {
    private lateinit var sp: SharedPreferences
    fun init(ctx: Context) {
        sp = ctx.getSharedPreferences("jarvis", Context.MODE_PRIVATE)
        // 1.0–1.1 kept a "listening" flag that 停止监听 set to false for good: nothing (opening the
        // app, a reboot) ever set it back, so a stopped phone stayed deaf. It is not carried over.
        if (sp.contains("listening")) sp.edit().remove("listening").apply()
    }

    const val THRESHOLD_DEFAULT = 0.25f
    const val THRESHOLD_MIN = 0.05f
    const val THRESHOLD_MAX = 0.95f

    /** The relay address exactly as the phone's browser opens it (secret path included). */
    var relayUrl: String?
        get() = sp.getString("relay_url", null)
        set(v) = sp.edit().putString("relay_url", v).apply()

    /** the owner pressed 停止监听. Opening the app then shows 「未在监听 · 点此开始」 instead of starting by
     *  itself, and a quiet notification keeps a 开始监听 button. Any start clears it. */
    var stoppedByUser: Boolean
        get() = sp.getBoolean("stopped_by_user", false)
        set(v) = sp.edit().putBoolean("stopped_by_user", v).apply()

    /** Wake-word score threshold: lower wakes more easily (and more falsely). */
    var threshold: Float
        get() = sp.getFloat("threshold", THRESHOLD_DEFAULT).coerceIn(THRESHOLD_MIN, THRESHOLD_MAX)
        set(v) = sp.edit().putFloat("threshold", v.coerceIn(THRESHOLD_MIN, THRESHOLD_MAX)).apply()

    /** Reserved, off, not implemented in v1: a wake never sends by itself. */
    var autoSend: Boolean
        get() = sp.getBoolean("auto_send", false)
        set(v) = sp.edit().putBoolean("auto_send", v).apply()

    /** Lock-screen notification for each new reply of 你的 Agent (ADR-050). On unless the owner turns it off. */
    var replyNotify: Boolean
        get() = sp.getBoolean("reply_notify", true)
        set(v) = sp.edit().putBoolean("reply_notify", v).apply()

    /** The newest turn id the reply watcher has dealt with (notified or seen on screen); -1 = never
     *  connected, so the first connection starts from the bridge's newest turn instead of the backlog. */
    var replySeen: Long
        get() = sp.getLong("reply_seen", -1L)
        set(v) = sp.edit().putLong("reply_seen", v).apply()

    var wakeTokens: String
        get() = sp.getString("wake_tokens", "") ?: ""
        set(v) = sp.edit().putString("wake_tokens", v).apply()
    var wakeEnabled: Boolean
        get()=sp.getBoolean("wake_enabled",false)
        set(v)=sp.edit().putBoolean("wake_enabled",v).apply()

    var speakNotifications: Boolean
        get()=sp.getBoolean("speak_notifications",false)
        set(v)=sp.edit().putBoolean("speak_notifications",v).apply()
    var ttsMode: String
        get()=sp.getString("tts_mode","phone")?:"phone"
        set(v)=sp.edit().putString("tts_mode",v).apply()
    var ttsVoice: String
        get()=sp.getString("tts_voice","")?:""
        set(v)=sp.edit().putString("tts_voice",v).apply()
    var ttsRate: Float
        get()=sp.getFloat("tts_rate",1f)
        set(v)=sp.edit().putFloat("tts_rate",v.coerceIn(0.5f,2f)).apply()
    var language: String
        get()=sp.getString("language","zh")?:"zh"
        set(v)=sp.edit().putString("language",v).apply()

    var lastWake: Long
        get() = sp.getLong("last_wake", 0L)
        set(v) = sp.edit().putLong("last_wake", v).apply()

    var lastWakeScore: Float
        get() = sp.getFloat("last_wake_score", 0f)
        set(v) = sp.edit().putFloat("last_wake_score", v).apply()
}

