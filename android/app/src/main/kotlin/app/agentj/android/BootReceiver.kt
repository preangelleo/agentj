package app.agentj.android

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log

/**
 * After a reboot or an app update. Android does not let a microphone service start from here
 * (while-in-use rule; Android 15 names BOOT_COMPLETED outright), so the reminder goes up first;
 * then, with "display over other apps", the app shows its orb overlay (a visible window) and tries to
 * start listening anyway — if that works, the service takes the reminder down again.
 * Stopped by the owner: only the quiet 开始监听 notification comes back (with the reply watcher behind it
 * when reply notifications are on).
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        Prefs.init(ctx)
        if (Prefs.relayUrl == null) return
        Log.i("JarvisBoot", "${intent.action}: stoppedByUser=${Prefs.stoppedByUser} listening=${Hub.state}")
        if (Prefs.stoppedByUser) {
            // Listening stays off; the reply notifications (ADR-050) need no microphone and may start here.
            if (Prefs.replyNotify) WakeService.replies(ctx.applicationContext) else Notices.stopped(ctx)
            return
        }
        if (Hub.state != Hub.Listen.OFF && Hub.state != Hub.Listen.ERROR) return
        Notices.resume(ctx, if (intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) "App 更新了，点一下恢复「配置的唤醒词」监听"
                            else "手机重启了，点一下恢复「配置的唤醒词」监听")
        Revive.start(ctx.applicationContext, if (intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) "update" else "boot", goAsync())
    }
}

/**
 * Starting the microphone service from the background (after an update or a reboot, or after the
 * system killed the app): Android only allows it while one of our windows is visible, so show
 * the orb overlay, wait until it has really been drawn, then start; refused anyway (ColorOS can
 * still say "started from background"), try again with the overlay still up — three tries over
 * about five seconds instead of waiting for the owner or for the system's next restart.
 */
object Revive {
    private const val TAG = "JarvisRevive"
    private const val TRIES = 3
    private val main = Handler(Looper.getMainLooper())

    fun start(app: Context, why: String, done: BroadcastReceiver.PendingResult? = null) {
        if (Prefs.stoppedByUser || Hub.on) { done?.finish(); return }
        if (!Settings.canDrawOverlays(app)) {
            Log.i(TAG, "$why: no overlay permission, one plain try")
            try { WakeService.start(app) } finally { done?.finish() }
            return
        }
        attempt(app, why, 1, done)
    }

    private fun attempt(app: Context, why: String, n: Int, done: BroadcastReceiver.PendingResult?) {
        Popup.showOrb(app, ListenOrb.Phase.READY) {
            main.postDelayed({
                WakeService.start(app)
                main.postDelayed({
                    when {
                        Hub.on -> { Log.i(TAG, "$why: listening (try $n)"); done?.finish() }
                        n < TRIES && !Prefs.stoppedByUser -> { Log.i(TAG, "$why: refused (try $n), again"); attempt(app, why, n + 1, done) }
                        else -> { Log.w(TAG, "$why: refused $n times; the 点一下恢复 notification stays"); done?.finish() }
                    }
                }, 1200)
            }, 100)
        }
    }
}

/**
 * Swiped away in recents: AOSP keeps a foreground service's process, but ColorOS kills it and the
 * system's own restart of the service can take tens of seconds. An exact alarm two seconds out
 * brings the app back through [Revive] instead; if the process survived it finds Hub.on and does
 * nothing.
 */
class ReviveReceiver : BroadcastReceiver() {
    companion object {
        const val ACTION = "app.agentj.android.REVIVE"

        fun schedule(ctx: Context, delayMs: Long = 2000) {
            val am = ctx.getSystemService(android.app.AlarmManager::class.java) ?: return
            val pi = PendingIntent.getBroadcast(ctx, 40, Intent(ctx, ReviveReceiver::class.java).setAction(ACTION),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val at = android.os.SystemClock.elapsedRealtime() + delayMs
            try {
                if (am.canScheduleExactAlarms()) am.setExactAndAllowWhileIdle(android.app.AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
                else am.setAndAllowWhileIdle(android.app.AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi)
                Log.i("JarvisRevive", "task removed: revive alarm in ${delayMs} ms (exact=${am.canScheduleExactAlarms()})")
            } catch (e: Exception) { Log.w("JarvisRevive", "alarm: $e") }
        }
    }

    override fun onReceive(ctx: Context, intent: Intent) {
        Prefs.init(ctx)
        if (Prefs.relayUrl == null) return
        Log.i("JarvisRevive", "alarm: listening=${Hub.state}")
        if (Hub.on) return
        if (Prefs.stoppedByUser) { WakeService.replies(ctx.applicationContext); return }
        Revive.start(ctx.applicationContext, "swipe", goAsync())
    }
}

/** The "not listening" notifications. One id: whichever is current replaces the other. */
object Notices {
    const val ID_RESUME = 3

    private fun open(ctx: Context): PendingIntent =
        PendingIntent.getActivity(ctx, 3, Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    /** 开始监听 straight from the notification, without opening the app (a notification tap is
     *  one of the few places a microphone service may start from). */
    private fun startAction(ctx: Context): Notification.Action =
        Notification.Action.Builder(null, "开始监听", PendingIntent.getForegroundService(ctx, 4,
            Intent(ctx, WakeService::class.java).setAction(WakeService.ACTION_START),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)).build()

    /** Listening should be on and is not (reboot, update, the system stopped it). */
    fun resume(ctx: Context, text: String) = post(ctx, Notification.Builder(ctx, JarvisApp.CH_NOTICE)
        .setContentTitle("Agent J 没在听")
        .setContentText(text)
        .setStyle(Notification.BigTextStyle().bigText(text))
        .setAutoCancel(true))

    /** the owner stopped it: a quiet, lasting way back. */
    fun stopped(ctx: Context) = post(ctx, Notification.Builder(ctx, JarvisApp.CH_STOPPED)
        .setContentTitle("Agent J 已停止监听")
        .setContentText("点「开始监听」恢复「配置的唤醒词」")
        .setOngoing(true))

    private fun post(ctx: Context, b: Notification.Builder) {
        val n = b.setSmallIcon(android.R.drawable.ic_btn_speak_now)
            .setContentIntent(open(ctx))
            .addAction(startAction(ctx))
            .setGroup("jarvis.resume")
            .build()
        try { ctx.getSystemService(NotificationManager::class.java).notify(ID_RESUME, n) } catch (_: SecurityException) {}
    }

    fun clear(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).cancel(ID_RESUME)
}
