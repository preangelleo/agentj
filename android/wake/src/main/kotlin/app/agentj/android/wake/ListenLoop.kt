package app.agentj.android.wake

/**
 * The service's whole audio brain, free of Android so it can be tested on a laptop:
 * LISTENING (wake word) → CAPTURING (VAD endpointing) → back to LISTENING.
 *
 * Feed it whatever the microphone hands over, any size, 16 kHz mono int16, from ONE thread.
 * [stop] may be called from any thread; it takes effect on the next [feed].
 */
class ListenLoop(
    private val engine: WakeDetector,
    private val vad: SileroVad,
    private val listener: Listener,
    @Volatile var threshold: Float = 0.5f,
    private val endpointer: () -> Endpointer = { Endpointer() },
) {
    interface Listener {
        fun onWake(score: Float)
        /** A take ended with speech in it. [reason] is SILENCE, MAX or MANUAL. */
        fun onTake(wav: ByteArray, seconds: Double, reason: String)
        /** A take ended without speech (NO_SPEECH) or was cancelled (CANCEL). */
        fun onEmpty(reason: String)
    }

    enum class Mode { LISTENING, CAPTURING }

    @Volatile var mode = Mode.LISTENING; private set
    @Volatile private var stopRequest: String? = null   // "MANUAL" or "CANCEL"

    private val wakeFrame = ShortArray(AudioSpec.FRAME)
    private var wakeFill = 0
    private val vadChunk = ShortArray(SileroVad.CHUNK)
    private var vadFill = 0
    private val take = PcmBuffer()
    private var ep = endpointer()

    fun stop() { if (mode == Mode.CAPTURING) stopRequest = "MANUAL" }
    fun cancel() { if (mode == Mode.CAPTURING) stopRequest = "CANCEL" }

    /** Start a take without the wake word (the notification's "说话" button). */
    fun startCapture() { if (mode == Mode.LISTENING) beginCapture() }

    fun feed(pcm: ShortArray, n: Int = pcm.size) {
        var i = 0
        while (i < n) {
            if (mode == Mode.LISTENING) {
                val k = minOf(n - i, wakeFrame.size - wakeFill)
                System.arraycopy(pcm, i, wakeFrame, wakeFill, k)
                wakeFill += k; i += k
                if (wakeFill == wakeFrame.size) {
                    wakeFill = 0
                    val s = engine.process(wakeFrame)
                    if (s >= threshold) {
                        listener.onWake(s)
                        beginCapture()
                    }
                }
            } else {
                stopRequest?.let { finish(it); return }
                val k = minOf(n - i, vadChunk.size - vadFill)
                System.arraycopy(pcm, i, vadChunk, vadFill, k)
                take.append(pcm.copyOfRange(i, i + k))
                vadFill += k; i += k
                if (vadFill == vadChunk.size) {
                    vadFill = 0
                    when (ep.feed(vad.probability(vadChunk))) {
                        Endpointer.Result.CONTINUE -> {}
                        Endpointer.Result.SILENCE -> { finish("SILENCE"); return }
                        Endpointer.Result.MAX -> { finish("MAX"); return }
                        Endpointer.Result.NO_SPEECH -> { finish("NO_SPEECH"); return }
                    }
                }
            }
        }
        if (mode == Mode.CAPTURING) stopRequest?.let { finish(it) }
    }

    private fun beginCapture() {
        mode = Mode.CAPTURING
        stopRequest = null
        take.clear(); vadFill = 0
        vad.reset(); ep = endpointer()
    }

    private fun finish(reason: String) {
        val heardSpeech = ep.started
        val wav = if (heardSpeech && reason != "CANCEL") take.toWav() else null
        val secs = take.seconds()
        take.clear()
        stopRequest = null
        engine.reset(); wakeFill = 0
        mode = Mode.LISTENING
        if (wav != null) listener.onTake(wav, secs, reason)
        else listener.onEmpty(if (reason == "CANCEL") "CANCEL" else "NO_SPEECH")
    }
}
