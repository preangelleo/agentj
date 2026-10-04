package app.agentj.android.wake
interface WakeDetector : AutoCloseable { fun process(frame: ShortArray): Float; fun reset() }
