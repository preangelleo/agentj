package app.agentj.android

import android.content.Context
import android.speech.tts.TextToSpeech
import android.os.Handler
import android.os.Looper
import java.util.Locale

/** Generic notification only. Never select a network voice or speak conversation on lockscreen. */
object ReplySpeech {
    private var engine: TextToSpeech?=null
    private var ready=false
    private val main=Handler(Looper.getMainLooper())
    fun init(ctx:Context){main.post{engine=TextToSpeech(ctx.applicationContext){status->ready=status==TextToSpeech.SUCCESS}}}
    fun stop(){main.post{engine?.stop()}}
    fun notifyReply(){main.post{
        val t=engine?:return@post
        if(!ready || !Prefs.speakNotifications || Prefs.ttsMode!="phone")return@post
        val lang=if(Prefs.language=="en")"en" else "zh"
        val local=t.voices?.filter{!it.isNetworkConnectionRequired}?:emptyList()
        val voice=if(Prefs.ttsVoice.isBlank()) local.firstOrNull{it.locale.language==lang || (lang=="zh" && it.locale.language=="cmn")}
                  else local.firstOrNull{it.name==Prefs.ttsVoice}
        if(voice==null){Hub.speechError="本地通知声音不可用 / Local notification voice unavailable";return@post}
        t.voice=voice;t.setSpeechRate(Prefs.ttsRate)
        val result=t.speak(if(lang=="en")"Your agent has replied." else "你的 Agent 有新回复。",TextToSpeech.QUEUE_FLUSH,null,"agentj-notification")
        Hub.speechError=if(result==TextToSpeech.ERROR)"通知播报失败 / Notification speech failed" else ""
    }}
}
