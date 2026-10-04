package app.agentj.android.wake

/**
 * Decides when a take is over, from per-chunk speech probabilities. Pure logic, no audio:
 *
 * - speech has "started" once [minSpeechMs] of chunks at or above [speechThreshold] have been heard;
 * - after that, [silenceMs] of consecutive chunks below the threshold ends the take (SILENCE);
 * - nothing that counts as speech within [noSpeechMs] of the start ends it empty (NO_SPEECH);
 * - [maxMs] is a hard cap (MAX).
 */
class Endpointer(
    val chunkMs: Int = 32,
    val speechThreshold: Float = 0.5f,
    val minSpeechMs: Int = 160,
    val silenceMs: Int = 1500,
    val noSpeechMs: Int = 6000,
    val maxMs: Int = 60_000,
) {
    enum class Result { CONTINUE, SILENCE, NO_SPEECH, MAX }

    var elapsedMs = 0; private set
    var speechMs = 0; private set
    var started = false; private set
    private var quietMs = 0

    fun feed(probability: Float): Result {
        elapsedMs += chunkMs
        if (probability >= speechThreshold) {
            speechMs += chunkMs
            quietMs = 0
            if (speechMs >= minSpeechMs) started = true
        } else {
            quietMs += chunkMs
        }
        return when {
            started && quietMs >= silenceMs -> Result.SILENCE
            !started && elapsedMs >= noSpeechMs -> Result.NO_SPEECH
            elapsedMs >= maxMs -> Result.MAX
            else -> Result.CONTINUE
        }
    }
}
