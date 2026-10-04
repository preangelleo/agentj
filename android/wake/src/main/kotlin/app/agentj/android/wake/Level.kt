package app.agentj.android.wake

import kotlin.math.log10
import kotlin.math.sqrt

/** Microphone loudness for the listening orb: 0 (quiet room) … 1 (speaking up close). */
object Level {
    /** dBFS mapped onto 0..1: FLOOR and below is silence, CEIL and above is full. */
    const val FLOOR_DB = -55.0
    const val CEIL_DB = -15.0

    fun of(pcm: ShortArray, n: Int = pcm.size): Float {
        val m = minOf(n, pcm.size)
        if (m <= 0) return 0f
        var sum = 0.0
        for (i in 0 until m) { val s = pcm[i] / 32768.0; sum += s * s }
        val rms = sqrt(sum / m)
        if (rms <= 0.0) return 0f
        val db = 20 * log10(rms)
        return ((db - FLOOR_DB) / (CEIL_DB - FLOOR_DB)).coerceIn(0.0, 1.0).toFloat()
    }
}
