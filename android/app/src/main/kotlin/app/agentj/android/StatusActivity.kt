package app.agentj.android

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.NotificationManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.WindowInsets
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Status and settings: is it listening, when did it last wake, is battery optimization off,
 * the permissions that make a wake reach the screen, sensitivity, the relay address.
 * Reached from the launcher icon's long-press shortcut, the listening notification, or on
 * first run (no address yet).
 */
class StatusActivity : Activity() {

    private val main = Handler(Looper.getMainLooper())
    private lateinit var col: LinearLayout
    private lateinit var listenState: TextView
    private lateinit var listenBtn: Button
    private lateinit var lastWake: TextView
    private lateinit var replyState: TextView
    private val rows = mutableListOf<Pair<() -> Boolean, Pair<TextView, Button>>>()
    private val watcher: () -> Unit = { refresh() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        build()
    }

    override fun onResume() {
        super.onResume()
        Hub.watch(watcher)
        refresh()
        tick()
    }

    override fun onPause() {
        Hub.unwatch(watcher)
        main.removeCallbacksAndMessages(null)
        super.onPause()
    }

    private fun tick() { refresh(); main.postDelayed({ tick() }, 1000) }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun section(title: String) = TextView(this).apply {
        text = title; textSize = 13f; setTextColor(Color.parseColor("#629887")); typeface = Typeface.DEFAULT_BOLD
        setPadding(0, dp(20), 0, dp(6))
    }.also { col.addView(it) }

    private fun line(textSize: Float = 16f) = TextView(this).apply {
        this.textSize = textSize; setTextColor(Color.parseColor("#202124")); setPadding(0, dp(4), 0, dp(4))
    }.also { col.addView(it) }

    private fun button(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label; isAllCaps = false; minHeight = dp(52); setOnClickListener { onClick() }
    }

    /** A permission-ish row: status text + a button that jumps to the system page. */
    private fun check(label: String, ok: () -> Boolean, fix: String, go: () -> Unit) {
        val row = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        val t = TextView(this).apply { textSize = 15f; setTextColor(Color.parseColor("#202124")) }
        val b = button(fix, go)
        row.addView(t, LinearLayout.LayoutParams(0, -2, 1f)); row.addView(b)
        col.addView(row)
        t.tag = label
        rows += ok to (t to b)
    }

    @SuppressLint("UseSwitchCompatOrMaterialCode")
    private fun build() {
        col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(20), dp(12), dp(20), dp(32)) }
        val scroll = ScrollView(this).apply { addView(col); setBackgroundColor(Color.WHITE) }
        setContentView(scroll)
        window.setDecorFitsSystemWindows(false)
        scroll.setOnApplyWindowInsetsListener { v, insets ->
            val b = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.ime() or WindowInsets.Type.displayCutout())
            v.setPadding(b.left, b.top, b.right, b.bottom); WindowInsets.CONSUMED
        }

        col.addView(TextView(this).apply {
            text = "Agent J"; textSize = 26f; typeface = Typeface.DEFAULT_BOLD; setTextColor(Color.parseColor("#202124"))
        })
        line(13f).text = "说「配置的唤醒词」→ 提示音 → 说话 → 停顿约 1.5 秒自动结束 → 文字填进输入框。永远不会自动发送。"

        section("监听")
        listenState = line(18f)
        lastWake = line()
        listenBtn = button("") {
            if (!Hub.on) {
                Prefs.stoppedByUser = false
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED)
                    requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
                else WakeService.start(this)
            } else WakeService.command(this, WakeService.ACTION_STOP)
            main.postDelayed({ refresh() }, 300)
        }
        col.addView(listenBtn)

        section("新回复通知")
        col.addView(Switch(this).apply {
            text = "你的 Agent每回复一条，锁屏上提醒一条"
            textSize = 15f; minHeight = dp(52)
            isChecked = Prefs.replyNotify
            setOnCheckedChangeListener { _, on ->
                Prefs.replyNotify = on
                // Replies that came while it was off are not news: start from the bridge's newest.
                if (on) Prefs.replySeen = -1L
                WakeService.replies(this@StatusActivity)
                main.postDelayed({ refresh() }, 300)
            }
        })
        replyState = line(15f)
        line(13f).text = "标题「你的 Agent」+ 回复开头约 80 字；点一下打开 App 并翻到那一条。App 开着、正在看时不提醒；" +
            "正在录音（唤醒、按住说话、别的 App 录音、通话）时静音。多条合并成一组。\n" +
            "停止监听后照常提醒：会常驻一条安静的「已停止监听」通知（那就是它在后台的样子）。" +
            "要 App 完全不在后台运行：停止监听并关掉这里。\n" +
            "锁屏上显示回复开头的文字；不想在锁屏露出内容，去系统设置 → 通知 → 锁屏里改成隐藏敏感内容。"

        section("让唤醒真的能叫醒屏幕（OPPO / ColorOS 必做）")
        check("电池优化", { isIgnoringBattery() }, "去豁免") {
            @SuppressLint("BatteryLife")
            val i = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
            go(i, Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
        check("麦克风", { granted(Manifest.permission.RECORD_AUDIO) }, "授权") {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 1)
        }
        check("通知", { notificationsOn() }, "打开") {
            if (Build.VERSION.SDK_INT >= 33 && !granted(Manifest.permission.POST_NOTIFICATIONS))
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
            else go(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
        }
        check("显示在其他应用上层（亮屏时唤醒直接弹出）", { Settings.canDrawOverlays(this) }, "去开") {
            go(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName")))
        }
        check("全屏通知（锁屏时唤醒亮屏）", { fullScreenOk() }, "去开") {
            if (Build.VERSION.SDK_INT >= 34)
                go(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:$packageName")))
            else go(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, packageName))
        }
        line(13f).text = "OPPO / ColorOS 另有一道「后台弹出界面」开关（应用详情 → 权限），这里查不到它的状态；" +
            "没开时亮屏唤醒只弹一条带声音的通知，点一下直达输入框。锁屏时唤醒会亮屏并直接弹出指纹 / 人脸解锁。"
        col.addView(button("打开本 App 的系统设置（自启动 / 后台运行 / 后台弹出界面）") {
            go(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
        })

        section("唤醒灵敏度")
        val thLabel = line(15f)
        thLabel.text = thresholdText()
        line(13f).text = "由主机配置同步：agentj config set voice.wake_threshold <0.05..0.95>。修改后做真实语音测试。"

        section("自动发送")
        val sw = Switch(this).apply {
            text = "唤醒后自动发送（本版未实现，保持关闭）"
            isChecked = false; isEnabled = false; textSize = 15f
        }
        col.addView(sw)

        section("Relay 网址")
        line(13f).text = "手机浏览器里打开的那个完整网址（含那段长路径）。只存在本机，不备份、不上传。"
        val addr = EditText(this).apply {
            hint = "https://…"
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI or InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setSingleLine(true)
            setText(Prefs.relayUrl?.let { masked(it) } ?: "")
            setOnFocusChangeListener { v, has -> if (has && Prefs.relayUrl != null && text.toString() == masked(Prefs.relayUrl!!)) (v as EditText).setText("") }
        }
        col.addView(addr)
        col.addView(button("保存并打开 Agent J") {
            val raw = addr.text.toString().trim()
            if (raw.isEmpty() || raw == Prefs.relayUrl?.let { masked(it) }) { openMain(); return@button }
            val u = Uri.parse(raw)
            if (u.scheme != "https" && !(BuildConfig.DEBUG && u.scheme == "http") || u.host.isNullOrEmpty()) {
                Toast.makeText(this, "要一个 https:// 开头的完整网址", Toast.LENGTH_LONG).show(); return@button
            }
            Prefs.relayUrl = raw
            addr.setText(masked(raw))
            openMain()
        })

        section("关于")
        line(13f).text = "版本 ${BuildConfig.VERSION_NAME}（${BuildConfig.VERSION_CODE}）。唤醒词：sherpa-onnx 中英离线关键词，跟随主机 Agent 名或自定义短语。" +
            "录下的话通过加密连接发给你自己的主机转写，和手机上按住说话走同一条路。\n" +
            "手机重启或 App 更新后，系统可能不让它自己恢复监听：会弹一条「Agent J 没在听」，点一下或打开 App 即恢复。" +
            "停了监听想恢复：打开 App 点顶部红条、通知里的「开始监听」，或下拉快捷设置里的「Agent J」磁贴。"
    }

    private fun toProgress(t: Float) = ((t - Prefs.THRESHOLD_MIN) / (Prefs.THRESHOLD_MAX - Prefs.THRESHOLD_MIN) * 100).roundToInt()
    private fun fromProgress(p: Int) = Prefs.THRESHOLD_MIN + (Prefs.THRESHOLD_MAX - Prefs.THRESHOLD_MIN) * p / 100f
    private fun thresholdText() = "阈值 %.2f —— 越低越容易唤醒，也越容易误唤醒（误唤醒只会填字，不会发送）".format(Prefs.threshold)

    private fun masked(url: String): String {
        val u = Uri.parse(url)
        return "${u.scheme}://${u.host}/••••••"
    }

    private fun openMain() {
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        finish()
    }

    private fun go(i: Intent, fallback: Intent? = null) {
        try { startActivity(i) } catch (_: Exception) {
            try { startActivity(fallback ?: Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName"))) } catch (_: Exception) {}
        }
    }

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
    private fun isIgnoringBattery() = getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
    private fun notificationsOn() = getSystemService(NotificationManager::class.java).areNotificationsEnabled()
    private fun fullScreenOk() = Build.VERSION.SDK_INT < 34 || getSystemService(NotificationManager::class.java).canUseFullScreenIntent()

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, perms, results)
        if (code == 1 && granted(Manifest.permission.RECORD_AUDIO) && !Prefs.stoppedByUser) WakeService.start(this)
        refresh()
    }

    private fun refresh() {
        if (!::listenState.isInitialized) return
        val (txt, color) = when (Hub.state) {
            Hub.Listen.LISTENING -> "● 正在监听「配置的唤醒词」" to "#2E7D32"
            Hub.Listen.CAPTURING -> "● 正在录音" to "#C62828"
            Hub.Listen.PAUSED -> "● 暂停（页面正在用麦克风）" to "#F9A825"
            Hub.Listen.YIELDED -> "● 暂停（键盘弹出中，让麦克风给语音输入；收起键盘即恢复）" to "#F9A825"
            Hub.Listen.STARTING -> "○ 正在启动…" to "#616161"
            Hub.Listen.ERROR -> "✕ ${Hub.error ?: "出错"}" to "#C62828"
            Hub.Listen.OFF -> (if (Prefs.stoppedByUser) "○ 已停止监听（你停的）" else "○ 没在监听") to "#616161"
        }
        listenState.text = txt; listenState.setTextColor(Color.parseColor(color))
        replyState.text = when {
            Hub.speechError.isNotBlank() -> "✕ ${Hub.speechError}"
            !Prefs.replyNotify -> "○ 已关闭"
            !notificationsOn() -> "✕ 系统通知被关了：下面「通知」一行去打开"
            !WakeService.alive -> "○ 未运行（打开一次 App 即恢复）"
            else -> "● ${Hub.replies}"
        }
        listenBtn.text = if (!Hub.on) "开始监听" else "停止监听"
        val lw = Prefs.lastWake
        lastWake.text = if (lw == 0L) "最近一次唤醒：—"
            else "最近一次唤醒：%s（分数 %.2f）".format(SimpleDateFormat("M月d日 HH:mm:ss", Locale.CHINA).format(Date(lw)), Prefs.lastWakeScore)
        for ((ok, tb) in rows) {
            val good = ok()
            tb.first.text = "${if (good) "✓" else "✕"} ${tb.first.tag}"
            tb.first.setTextColor(Color.parseColor(if (good) "#2E7D32" else "#C62828"))
            tb.second.visibility = if (good) View.GONE else View.VISIBLE
        }
    }
}
