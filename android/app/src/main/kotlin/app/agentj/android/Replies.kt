package app.agentj.android

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.webkit.WebView
import android.webkit.WebViewClient
import android.webkit.WebSettings
import org.json.JSONObject
import org.json.JSONTokener

/** Reuses the paired encrypted phone client, not relay's authenticated plaintext HTTP routes.
 * Only a completed-turn ID and generic notification preferences cross from the local WebView.
 * Background WebView availability depends on Android; there is no FCM or plaintext server push.
 */
class ReplyWatcher(private val ctx: Context, private val recording: () -> Boolean) {
    companion object {
        const val GROUP="agentj.reply"
        @Volatile var current: ReplyWatcher?=null;private set
        fun kick(){current?.reload()}
        fun clear(ctx:Context){ctx.getSystemService(NotificationManager::class.java).activeNotifications.filter{it.notification.group==GROUP}.forEach{ctx.getSystemService(NotificationManager::class.java).cancel(it.id)}}
    }
    private val main=Handler(Looper.getMainLooper())
    private var web:WebView?=null
    private var running=false
    @Suppress("SetJavaScriptEnabled")
    fun start(){
        current=this;running=true
        main.post{
            val w=WebView(ctx);web=w
            w.settings.javaScriptEnabled=true;w.settings.domStorageEnabled=true
            w.settings.allowFileAccess = false
            w.settings.allowContentAccess = false
            w.settings.mixedContentMode=WebSettings.MIXED_CONTENT_NEVER_ALLOW
            w.settings.mediaPlaybackRequiresUserGesture=true
            w.webViewClient=object:WebViewClient(){override fun shouldOverrideUrlLoading(view:WebView,url:String):Boolean=originOf(url)!="https://m.agentj.app" && originOf(url)!="https://alpha-web.agentjarvis.net"}
            reload();tick()
        }
    }
    fun stop(){running=false;if(current===this)current=null;main.post{web?.destroy();web=null}}
    fun kick(){reload()}
    private fun reload(){main.post{
        val base=Prefs.relayUrl?:return@post
        if(originOf(base)!="https://m.agentj.app" && originOf(base)!="https://alpha-web.agentjarvis.net")return@post
        // Pairing material is entered only in MainActivity; the hidden client resumes local IndexedDB keys.
        val page=android.net.Uri.parse(base.substringBefore('#')).buildUpon().appendQueryParameter("watcher","1").build().toString()
        web?.loadUrl(page)
    }}
    private fun tick(){
        if(!running)return
        web?.evaluateJavascript("JSON.stringify(window.agentjNative?.snapshot?.()||{})"){result->
            try{
                val raw=JSONTokener(result).nextValue() as? String
                if(raw!=null){val v=JSONObject(raw);if(!v.optBoolean("ready",false))return@evaluateJavascript;Prefs.wakeTokens=v.optString("wake_tokens","");Prefs.wakeEnabled=v.optBoolean("wake_enabled",false)
                    Prefs.threshold=v.optDouble("wake_threshold",0.25).toFloat()
                    Prefs.speakNotifications=v.optBoolean("speak_notifications",false)
                    Prefs.ttsMode=v.optString("tts_mode","phone")
                    Prefs.ttsVoice=v.optString("tts_voice","")
                    Prefs.ttsRate=v.optDouble("tts_rate",1.0).toFloat()
                    Prefs.language=v.optString("language","zh")
                    val latest=v.optLong("latest",0)
                    if(latest>0){
                        if(Prefs.replySeen>=0 && latest>Prefs.replySeen && !MainActivity.visible)post(latest)
                        Prefs.replySeen=maxOf(Prefs.replySeen,latest)
                    }
                }
            }catch(_:Exception){}
        }
        main.postDelayed({tick()},2000)
    }
    private fun post(id:Long){
        val nm=ctx.getSystemService(NotificationManager::class.java)
        val intent=Intent(ctx,MainActivity::class.java).putExtra(MainActivity.EXTRA_TURN,id).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val tap=PendingIntent.getActivity(ctx,(id%100000).toInt(),intent,PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n=Notification.Builder(ctx,JarvisApp.CH_REPLY).setSmallIcon(app.agentj.android.R.drawable.ic_tile_mic)
            .setContentTitle("Agent J").setContentText("你的 Agent 有新回复 / New reply")
            .setContentIntent(tap).setAutoCancel(true).setVisibility(Notification.VISIBILITY_PRIVATE)
            .setGroup(GROUP).setGroupAlertBehavior(if(recording())Notification.GROUP_ALERT_SUMMARY else Notification.GROUP_ALERT_CHILDREN).build()
        nm.notify((id%100000).toInt()+1000,n)
        if(!recording())ReplySpeech.notifyReply()
    }
}
object ReplyText {
    const val EXCERPT = 80

    /** "https://host/r/<uuid>/…" → "https://host/r/<uuid>": every route lives under it. */
    fun base(url: String): String? {
        val u = try { java.net.URI(url.trim()) } catch (_: Exception) { return null }
        val scheme = u.scheme?.lowercase() ?: return null
        if (scheme != "https" && !(BuildConfig.DEBUG && scheme == "http")) return null
        val host = u.host ?: return null
        val segs = (u.rawPath ?: "").split('/').filter { it.isNotEmpty() }
        val i = segs.indexOf("r")
        if (i < 0 || i + 1 >= segs.size) return null
        val port = if (u.port == -1) "" else ":${u.port}"
        return "$scheme://$host$port/" + segs.subList(0, i + 2).joinToString("/")
    }

    /** The first ~80 characters of a Markdown reply as plain words on one line. */
    fun excerpt(md: String, max: Int = EXCERPT): String {
        var s = md.replace(Regex("```[^\\n]*\\n"), " ").replace("```", " ")
        s = s.replace(Regex("!?\\[([^\\]]*)]\\([^)]*\\)"), "$1")        // links / images → their text
        s = s.replace(Regex("(?m)^\\s{0,3}(#{1,6}\\s+|>\\s?|[-*+]\\s+|\\d+[.)]\\s+)"), "")
        s = s.replace(Regex("[*_`~]"), "").replace(Regex("\\|"), " ")
        s = s.replace(Regex("\\s+"), " ").trim()
        if (s.isEmpty()) return "（新回复）"
        val cps = s.codePointCount(0, s.length)
        return if (cps <= max) s else s.substring(0, s.offsetByCodePoints(0, max)).trimEnd() + "…"
    }
}

/** 2 s, 4 s, 8 s … up to 5 min, ±20 % so a restarted bridge is not hit by every phone at once. */
class Backoff(private val min: Long = 2_000, private val max: Long = 300_000, private val rnd: () -> Double = Math::random) {
    private var n = 0
    fun reset() { n = 0 }
    fun next(): Long {
        val base = minOf(max, min shl minOf(n, 20))
        n++
        return (base * (0.8 + 0.4 * rnd())).toLong().coerceAtMost(max)
    }
}
