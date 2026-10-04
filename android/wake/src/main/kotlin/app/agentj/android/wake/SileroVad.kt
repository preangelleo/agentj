package app.agentj.android.wake

/** 512-sample speech probability contract; the app owns the native implementation. */
interface SileroVad : AutoCloseable {
    companion object { const val CHUNK = 512 }
    fun reset()
    fun probability(chunk: ShortArray): Float
}
