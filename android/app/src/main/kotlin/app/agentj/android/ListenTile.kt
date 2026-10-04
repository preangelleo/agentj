package app.agentj.android

import android.app.PendingIntent
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * Quick Settings tile 「Agent J」: one tap starts or stops the wake word. If the system will not
 * let the microphone start from here, the tap opens the app instead (it starts on screen).
 */
class ListenTile : TileService() {
    private val main = Handler(Looper.getMainLooper())
    private val watcher: () -> Unit = { update() }

    override fun onStartListening() {
        Prefs.init(this)
        Hub.watch(watcher)
        update()
    }

    override fun onStopListening() { Hub.unwatch(watcher) }

    override fun onClick() {
        Prefs.init(this)
        if (Hub.on) { WakeService.command(this, WakeService.ACTION_STOP); return }
        Prefs.stoppedByUser = false
        WakeService.start(this)
        main.postDelayed({ if (!Hub.on) openApp() }, 1500)
    }

    private fun openApp() {
        val i = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (Build.VERSION.SDK_INT >= 34)
            startActivityAndCollapse(PendingIntent.getActivity(this, 5, i, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
        else @Suppress("DEPRECATION", "StartActivityAndCollapseDeprecated") startActivityAndCollapse(i)   // API 31–33 only
    }

    private fun update() {
        val t = qsTile ?: return
        t.state = if (Hub.on) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        t.label = "Agent J"
        t.subtitle = when {
            Hub.state == Hub.Listen.CAPTURING -> "在录音"
            Hub.state == Hub.Listen.PAUSED || Hub.state == Hub.Listen.YIELDED -> "暂停"
            Hub.on -> "在听"
            else -> "已停止"
        }
        t.icon = Icon.createWithResource(this, R.drawable.ic_tile_mic)
        t.updateTile()
    }
}
