package app.agentj.android

import android.app.ActivityOptions
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.Gravity
import android.view.WindowManager

/**
 * Bringing the app forward after a wake, within Android's background-activity-start rules.
 *
 * A plain startActivity from the service is blocked (BAL_BLOCK: a foreground service is not an
 * exemption, and since Android 15 "display over other apps" only counts while the app actually
 * has an overlay window on screen). So:
 *  1. screen off / locked — the capture notification's full-screen intent (the system starts it);
 *  2. screen on, "display over other apps" granted — show the listening orb as an overlay in
 *     the middle of the screen, which makes the app visible, then send our own PendingIntent with the background-start
 *     opt-in on both the creator and the sender side;
 *  3. anything else — the heads-up notification: one tap opens the input field.
 * ROMs may add their own gate on top (ColorOS: 「后台弹出界面」); then only 1 and 3 remain.
 */
object Popup {
    private const val TAG = "JarvisPopup"
    private const val ORB_MS = 4000L
    /** ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOWED (API 33+). */
    private const val BAL_ALLOWED = 1
    /** ActivityOptions.MODE_BACKGROUND_ACTIVITY_START_ALLOW_ALWAYS (API 36; ALLOWED is deprecated there). */
    private const val BAL_ALLOW_ALWAYS = 3

    private val main = Handler(Looper.getMainLooper())
    private var orb: ListenOrb? = null

    private fun balMode() = if (Build.VERSION.SDK_INT >= 36) BAL_ALLOW_ALWAYS else BAL_ALLOWED

    fun openIntent(ctx: Context, fromWake: Boolean): Intent =
        Intent(ctx, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)
            .putExtra(MainActivity.EXTRA_FROM_WAKE, fromWake)

    /** PendingIntent to MainActivity; the creator opts in to background starts (API 34+). */
    fun pending(ctx: Context, code: Int, fromWake: Boolean): PendingIntent {
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val opts: Bundle? = if (Build.VERSION.SDK_INT >= 34)
            ActivityOptions.makeBasic().setPendingIntentCreatorBackgroundActivityStartMode(balMode()).toBundle()
        else null
        return PendingIntent.getActivity(ctx, code, openIntent(ctx, fromWake), flags, opts)
    }

    /** Called on the main thread by the service on a wake and when a take is ready. */
    fun bringForward(ctx: Context, phase: ListenOrb.Phase) {
        if (MainActivity.visible) return
        if (!Settings.canDrawOverlays(ctx)) { Log.i(TAG, "no overlay permission: notification only"); return }
        val shown = showOrb(ctx.applicationContext, phase)
        // Start only once the orb is really on screen (the exemption checks visibility).
        main.postDelayed({ send(ctx) }, if (shown) 150 else 0)
    }

    private fun send(ctx: Context) {
        if (MainActivity.visible) return
        try {
            // API 34+: the sender must opt in too (the call does not exist on 33, nor is it needed).
            val opts = if (Build.VERSION.SDK_INT >= 34)
                ActivityOptions.makeBasic().setPendingIntentBackgroundActivityStartMode(balMode()).toBundle()
            else null
            pending(ctx, 30, true).send(ctx, 0, null, null, null, null, opts)
            Log.i(TAG, "sent (orb=${orb != null})")
        } catch (e: Exception) { Log.w(TAG, "send: $e") }
    }

    /**
     * The overlay: the listening orb (ListenOrb) in the middle of the screen. Being a visible
     * window is what lets the app start things from the background; it also tells the owner at a
     * glance that Agent J heard him. Not touchable: it never gets in the way of what is below.
     * Called again while showing, it only changes phase and restarts its timer.
     * [onDrawn] runs once the window has really been drawn on screen (or after 800 ms at most):
     * only from then on does it count as a visible window for the system's background rules.
     */
    fun showOrb(ctx: Context, phase: ListenOrb.Phase = ListenOrb.Phase.LISTENING, onDrawn: (() -> Unit)? = null): Boolean {
        orb?.let { o ->
            o.set(phase); main.removeCallbacks(hide); main.postDelayed(hide, ORB_MS)
            onDrawn?.let { main.post(it) }
            return true
        }
        return try {
            val o = ListenOrb(ctx) { Hub.level }.apply { reset(phase) }
            val wm = ctx.getSystemService(WindowManager::class.java)
            // Placed at the true center of the display (Gravity.CENTER would center it above the
            // navigation bar) — the same spot as the app's own orb, so the hand-over is seamless.
            val screen = wm.currentWindowMetrics.bounds
            val side = ListenOrb.side(ctx.resources.displayMetrics.density, screen.width(), screen.height())
            val lp = WindowManager.LayoutParams(side, side,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT).apply {
                gravity = Gravity.TOP or Gravity.START
                x = (screen.width() - side) / 2; y = (screen.height() - side) / 2
                layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
            }
            wm.addView(o, lp)
            o.appear()
            orb = o
            main.postDelayed(hide, ORB_MS)
            if (onDrawn != null) {
                var once = false
                val fire = { if (!once) { once = true; onDrawn() } }
                try { o.viewTreeObserver.registerFrameCommitCallback { main.post(fire) } } catch (_: Exception) {}
                main.postDelayed(fire, 800)
            }
            true
        } catch (e: Exception) { Log.w(TAG, "overlay: $e"); onDrawn?.let { main.post(it) }; false }
    }

    private val hide = Runnable { hideOrb(immediate = false) }

    val orbShowing get() = orb != null

    /** Takes the overlay down; true if it was showing (MainActivity then shows its own orb without
     *  a second entrance, so the hand-over looks like one orb). */
    fun hideOrb(immediate: Boolean = true): Boolean {
        main.removeCallbacks(hide)
        val o = orb ?: return false
        orb = null
        val remove = { try { o.context.getSystemService(WindowManager::class.java).removeViewImmediate(o) } catch (_: Exception) {} }
        if (immediate) remove() else o.vanish(remove)
        return true
    }
}
