package app.agentj.android

import android.content.res.AssetManager
import app.agentj.android.wake.SileroVad
import com.k2fsa.sherpa.onnx.Vad
import com.k2fsa.sherpa.onnx.VadModelConfig
import com.k2fsa.sherpa.onnx.SileroVadModelConfig

/** Use the same pinned native runtime as keyword spotting, without a second ORT JNI ABI. */
class SherpaVad(assets: AssetManager) : SileroVad {
    private val detector = Vad(assets, VadModelConfig(
        sileroVadModelConfig=SileroVadModelConfig(model="models/silero_vad.onnx",windowSize=512),
        sampleRate=16000,numThreads=1,provider="cpu"))
    override fun probability(chunk: ShortArray): Float {
        require(chunk.size==SileroVad.CHUNK)
        return detector.compute(FloatArray(chunk.size){chunk[it]/32768f})
    }
    override fun reset() = detector.reset()
    override fun close() = detector.release()
}
