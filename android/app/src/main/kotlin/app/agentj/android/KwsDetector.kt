package app.agentj.android

import android.content.res.AssetManager
import app.agentj.android.wake.WakeDetector
import com.k2fsa.sherpa.onnx.KeywordSpotter
import com.k2fsa.sherpa.onnx.KeywordSpotterConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig

/** Offline bilingual streaming keyword spotting; text phonemes arrive only via the Noise session. */
class KwsDetector(private val assets: AssetManager) : WakeDetector {
    private fun newModel() = KeywordSpotter(assets, KeywordSpotterConfig(
        modelConfig = OnlineModelConfig(
            transducer=OnlineTransducerModelConfig(
                encoder="models/encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx",
                decoder="models/decoder-epoch-13-avg-2-chunk-16-left-64.onnx",
                joiner="models/joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx"),
            tokens="models/tokens.txt",numThreads=1,provider="cpu"),
        keywordsFile="models/keywords.txt",keywordsThreshold=Prefs.threshold))
    private var threshold=Prefs.threshold
    private var model=newModel()
    private var keyword=""
    private var stream: com.k2fsa.sherpa.onnx.OnlineStream?=null
    override fun process(frame: ShortArray): Float {
        if(threshold!=Prefs.threshold){stream?.release();stream=null;model.release();threshold=Prefs.threshold;model=newModel();keyword=""}
        val wanted=Prefs.wakeTokens
        if(wanted!=keyword){stream?.release();stream=null;keyword=wanted
            if(wanted.isNotBlank())stream=model.createStream(wanted)
        }
        if(!Prefs.wakeEnabled)return 0f
        val s=stream?:return 0f
        s.acceptWaveform(FloatArray(frame.size){frame[it]/32768.0f},16000)
        while(model.isReady(s)){
            model.decode(s)
            if(model.getResult(s).keyword=="agentj"){model.reset(s);return 1f}
        }
        return 0f
    }
    override fun reset(){stream?.let{model.reset(it)}}
    override fun close(){stream?.release();model.release()}
}
