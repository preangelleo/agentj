package app.agentj.android

import android.os.Handler
import android.os.Looper
import java.util.concurrent.CopyOnWriteArraySet

/** In-process meeting point of the service (audio thread) and the activities (main thread). */
object Hub {
    @Volatile var speechError: String=""
    enum class Listen { OFF, STARTING, LISTENING, CAPTURING, PAUSED, YIELDED, ERROR }

    class Take(val wav: ByteArray, val seconds: Double, val at: Long = System.currentTimeMillis())

    @Volatile var state = Listen.OFF; private set
    @Volatile var error: String? = null; private set
    /** The page is recording a hold-to-talk take itself: give it the microphone. */
    @Volatile var pausedForPage = false
    /** Another recorder started while the app was on screen: give it the microphone. */
    @Volatile var yielding = false
    /** The keyboard is up in Agent J: its voice typing (Gboard) fails if it starts while we hold the
     *  microphone, even if we let go a moment later — so let go as soon as the keyboard shows. */
    @Volatile var keyboardUp = false
    /** Microphone loudness while a take records (0..1, wake.Level); 0 otherwise. Drives the orb. */
    @Volatile var level = 0f
    @Volatile var note: String? = null      // one-shot message for the visible activity
    /** The reply watcher's line to the relay, in words for the status page (ADR-050). */
    @Volatile var replies = "关闭"

    private val main = Handler(Looper.getMainLooper())
    private val watchers = CopyOnWriteArraySet<() -> Unit>()
    private val takes = ArrayDeque<Take>()

    /** The listening service is up (in any state but off / failed). */
    val on get() = state != Listen.OFF && state != Listen.ERROR

    fun watch(w: () -> Unit) { watchers += w }
    fun unwatch(w: () -> Unit) { watchers -= w }
    fun changed() = main.post { watchers.forEach { it() } }

    fun set(s: Listen, err: String? = null) {
        if (state == s && error == err) return
        android.util.Log.i("JarvisHub", "state $state -> $s${err?.let { " ($it)" } ?: ""}")
        state = s; error = err; changed()
    }

    fun say(msg: String) { note = msg; changed() }

    @Synchronized fun postTake(t: Take) { takes.addLast(t); while (takes.size > 3) takes.removeFirst(); changed() }
    @Synchronized fun peekTake(): Take? = takes.firstOrNull()
    @Synchronized fun dropTake(t: Take) { takes.remove(t); changed() }
    @Synchronized fun pendingTakes(): Int = takes.size
}
