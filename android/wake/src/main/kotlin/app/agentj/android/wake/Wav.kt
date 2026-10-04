package app.agentj.android.wake

import java.nio.ByteBuffer
import java.nio.ByteOrder

/** 16-bit mono PCM ⇄ RIFF/WAVE. The bridge's /transcribe accepts audio/wav as is. */
object Wav {
    fun encode(pcm: ShortArray, count: Int = pcm.size, sampleRate: Int = 16_000): ByteArray {
        val dataLen = count * 2
        val b = ByteBuffer.allocate(44 + dataLen).order(ByteOrder.LITTLE_ENDIAN)
        b.put("RIFF".toByteArray()).putInt(36 + dataLen).put("WAVE".toByteArray())
        b.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
            .putInt(sampleRate).putInt(sampleRate * 2).putShort(2).putShort(16)
        b.put("data".toByteArray()).putInt(dataLen)
        for (i in 0 until count) b.putShort(pcm[i])
        return b.array()
    }

    /** Minimal reader for 16-bit mono PCM WAV (tests, fixtures). */
    fun decode(bytes: ByteArray): ShortArray {
        val b = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        var p = 12
        while (p + 8 <= bytes.size) {
            val id = String(bytes, p, 4)
            val len = b.getInt(p + 4)
            if (id == "data") {
                val n = minOf(len, bytes.size - p - 8) / 2
                return ShortArray(n) { b.getShort(p + 8 + it * 2) }
            }
            p += 8 + len + (len and 1)
        }
        error("no data chunk")
    }
}

/** Accumulates PCM for one take; grows as needed. */
class PcmBuffer(initial: Int = 16_000 * 10) {
    private var data = ShortArray(initial)
    var size = 0; private set
    fun append(src: ShortArray, n: Int = src.size) {
        if (size + n > data.size) data = data.copyOf(maxOf(data.size * 2, size + n))
        System.arraycopy(src, 0, data, size, n); size += n
    }
    fun toWav(): ByteArray = Wav.encode(data, size)
    fun seconds(): Double = size / 16_000.0
    fun clear() { size = 0 }
}
