package app.agentj.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager

class JarvisApp : Application() {
    companion object {
        const val CH_LISTEN = "listen"
        const val CH_WAKE = "wake"
        const val CH_NOTICE = "notice"
        const val CH_READY = "ready"
        const val CH_STOPPED = "stopped"
        const val CH_REPLY = "reply"
    }

    override fun onCreate() {
        super.onCreate()
        Prefs.init(this)
        ReplySpeech.init(this)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CH_LISTEN, "常驻监听", NotificationManager.IMPORTANCE_LOW).apply {
            description = "在后台听「配置的唤醒词」时的常驻通知"; setShowBadge(false)
        })
        nm.createNotificationChannel(NotificationChannel(CH_WAKE, "唤醒录音", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "唤醒后正在录音；用于把 App 拉到前台"; setSound(null, null); enableVibration(false)
        })
        nm.createNotificationChannel(NotificationChannel(CH_READY, "说完了", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "唤醒录音结束、App 没能自己弹出来时提醒你点开；带声音"
        })
        nm.createNotificationChannel(NotificationChannel(CH_NOTICE, "提醒", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "重启后恢复监听等提醒"
        })
        nm.createNotificationChannel(NotificationChannel(CH_REPLY, "你的 Agent的回复", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "你的 Agent每回复一条就提醒一次（锁屏可见，带声音；正在录音时静音）。开关在 App 状态与设置里"
            lockscreenVisibility = android.app.Notification.VISIBILITY_PUBLIC
        })
        nm.createNotificationChannel(NotificationChannel(CH_STOPPED, "已停止监听", NotificationManager.IMPORTANCE_LOW).apply {
            description = "你停掉监听后留一条安静的通知，带「开始监听」按钮"; setShowBadge(false)
        })
    }
}
