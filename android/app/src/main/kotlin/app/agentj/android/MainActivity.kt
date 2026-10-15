package app.agentj.android

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.KeyguardManager
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Base64
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.webkit.CookieManager
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import android.window.OnBackInvokedDispatcher

/**
 * The shell: a full-screen WebView on the relay PWA (same site, same login, same UI as the
 * phone's browser) plus the only things a page cannot do — a wake word in the background and
 * handing the recorded take to the page (window.relayNative.take, ADR-038). Send stays a tap.
 */
class MainActivity : Activity() {

    companion object {
        private const val TAG = "JarvisMain"
        const val EXTRA_FROM_WAKE = "from_wake"
        /** A reply notification was tapped: open that turn's page (ADR-050). */
        const val EXTRA_TURN = "turn"
        /** dp: from the disc's edge to the buttons; button height; the page's bottom bar (quick
         *  buttons + input) the buttons must stay clear of; blur radius of the page behind. */
        private const val BUTTON_GAP = 24
        private const val BUTTON_H = 52
        private const val INPUT_AREA = 160
        private const val BLUR = 12
        private const val REQ_PERMS = 1
        private const val REQ_FILE = 2
        private const val TAKE_TTL_MS = 5 * 60_000L

        /** The page's ≡ menu (in-app only) navigates here to open the native status page. */
        private const val APP_SCHEME = "agentj-app"
        private const val REQ_CAMERA = 3
        private const val PICK_MAX = 25L * 1024 * 1024 + 1     // one past the relay's cap: the page says "too large"

        @Volatile var visible = false; private set
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var root: FrameLayout
    private lateinit var web: WebView
    /** The listening orb in the middle of the screen, over a dark scrim (and the page blurred), with
     *  说完了 / 取消 right under it. */
    private lateinit var orbLayer: FrameLayout
    private lateinit var scrim: View
    private lateinit var page: View
    private lateinit var orbColumn: LinearLayout
    private lateinit var orb: ListenOrb
    private lateinit var orbButtons: LinearLayout
    /** System bars / keyboard insets as last applied (the orb is placed against the whole screen). */
    private var insetTop = 0
    private var insetBottom = 0
    private var insetLeft = 0
    private var insetRight = 0
    /** A wake take is recording: the keyboard stays down (it used to take the microphone). */
    private var imeHeld = false
    /** The capture the orb showed has ended (cancelled / empty): don't bring it back for the same one. */
    private var captureEnded = false
    /** Bumped for every delivered take, so only the newest one's transcription drives the orb. */
    private var asrWatch = 0
    private lateinit var cover: TextView
    /** 「未在监听 · 点此开始」: above the page (never over it) whenever the wake word is off. */
    private lateinit var offBanner: TextView
    private var origin: String = ""
    private var delivering = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var cameraFile: java.io.File? = null
    /** Locked-phone wake: ask for the fingerprint / face as soon as our window has focus. */
    private var wantUnlock = false
    private var themeColor = Color.WHITE
    private var bandColor = Color.WHITE
    /** Root background: the page colour, and under the gesture bar a strip in the page colour
     *  with the water composited on it (BarBand) — the page itself stops above that strip. */
    private val barsBg = android.graphics.drawable.LayerDrawable(arrayOf(
        android.graphics.drawable.ColorDrawable(Color.WHITE), android.graphics.drawable.ColorDrawable(Color.WHITE))).apply {
        setLayerGravity(1, Gravity.BOTTOM or Gravity.FILL_HORIZONTAL); setLayerHeight(1, 0)
    }
    private var orbInstant = false
    /** A tapped reply notification's turn, until the page has opened it. */
    private var wantTurn = 0L
    private var orbHiding = false
    private val watcher: () -> Unit = { refresh() }

    // ---- lifecycle -------------------------------------------------------------------

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = Prefs.relayUrl
        if (url == null) {
            startActivity(Intent(this, StatusActivity::class.java))
            finish(); return
        }
        origin = originOf(url)
        buildViews()
        setupWebView()
        if (savedInstanceState != null) web.restoreState(savedInstanceState) else web.loadUrl(url)
        askPermissions()
        handleWake(intent)
        handleTurn(intent)
        debugFeed(intent)
        if (Build.VERSION.SDK_INT >= 33) {
            onBackInvokedDispatcher.registerOnBackInvokedCallback(OnBackInvokedDispatcher.PRIORITY_DEFAULT) { back() }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (!hasFocus || !::cover.isInitialized) return
        if (cover.visibility == View.VISIBLE) {
            if (!getSystemService(KeyguardManager::class.java).isKeyguardLocked) unlocked()
            else if (wantUnlock) { wantUnlock = false; requestUnlock() }
        } else deliverPending()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleWake(intent)
        handleTurn(intent)
        debugFeed(intent)
    }

    private fun handleTurn(i: Intent?) {
        val id = i?.getLongExtra(EXTRA_TURN, 0L) ?: 0L
        if (id <= 0) return
        i?.removeExtra(EXTRA_TURN)
        wantTurn = id
        showTurn()
    }

    /** Ask the page to open [wantTurn]; until it is loaded (a cold start, a slept WebView) try again. */
    private fun showTurn(n: Int = 0) {
        val id = wantTurn
        if (id <= 0 || !::web.isInitialized) return
        if (originOf(web.url ?: "") != origin) { if (n < 30) main.postDelayed({ showTurn(n + 1) }, 500); return }
        web.evaluateJavascript("(function(){try{return !!(window.relayNative&&window.relayNative.show&&window.relayNative.show($id))}catch(e){return false}})()") { r ->
            if (id != wantTurn) return@evaluateJavascript
            if (r == "true") { Log.i(TAG, "reply notification: opened turn $id"); wantTurn = 0 }
            else if (n < 30) main.postDelayed({ showTurn(n + 1) }, 500)
            else { Log.w(TAG, "reply notification: the page never opened turn $id"); wantTurn = 0 }
        }
    }

    /** Debug builds only: `am start … --es debug_feed x.wav` plays a file from filesDir through the real pipeline. */
    private fun debugFeed(i: Intent?) {
        if (!BuildConfig.DEBUG) return
        val name = i?.getStringExtra(WakeService.EXTRA_DEBUG_FEED) ?: return
        i.removeExtra(WakeService.EXTRA_DEBUG_FEED)
        startForegroundService(Intent(this, WakeService::class.java).setAction(WakeService.ACTION_START)
            .putExtra(WakeService.EXTRA_DEBUG_FEED, name)
            .putExtra(WakeService.EXTRA_DEBUG_DELAY, i.getLongExtra(WakeService.EXTRA_DEBUG_DELAY, 0L)))
    }

    override fun onResume() {
        super.onResume()
        if (!::web.isInitialized) return
        visible = true
        // Taking over from the overlay orb: ours appears in the same place without a second entrance,
        // and the overlay fades only once our first frames are up (before that the window is blank).
        orbInstant = Popup.orbShowing
        if (orbInstant) main.postDelayed({ Popup.hideOrb(immediate = false) }, 400)
        getSystemService(android.app.NotificationManager::class.java).apply {
            cancel(WakeService.ID_CAPTURE); cancel(WakeService.ID_READY)
        }
        // On screen: the replies are in front of him (ADR-050); and the page may just have logged
        // in again, so the watcher tries its line now rather than after its backoff.
        ReplyWatcher.clear(this)
        ReplyWatcher.kick()
        Hub.watch(watcher)
        web.onResume()
        if (cover.visibility == View.VISIBLE) watchUnlock()
        startListeningIfWanted()
        // Not listening by choice (or no microphone): the reply watcher runs on its own.
        if (!WakeService.alive && (Prefs.stoppedByUser || !micGranted())) WakeService.replies(this)
        refresh()
        orbInstant = false
        tick()
        if (wantTurn > 0) showTurn()
    }

    /** Dark / light switched, folded / unfolded, rotated: the scrim follows (the orb re-measures itself). */
    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        if (::orbLayer.isInitialized) scrim.setBackgroundColor(scrimColor())
    }

    override fun onPause() {
        visible = false
        Hub.unwatch(watcher)
        if (::web.isInitialized) {
            web.onPause()
            CookieManager.getInstance().flush()
        }
        Hub.pausedForPage = false          // the page cannot be recording once we are not visible
        keyboard(false)
        main.removeCallbacksAndMessages(null)
        delivering = false
        asrWatch++
        if (::orbLayer.isInitialized) {
            orb.animate().cancel(); scrim.animate().cancel()
            orbLayer.visibility = View.GONE; orbHiding = false; blur(false)
        }
        super.onPause()
    }

    override fun onStop() {
        setShowWhenLocked(false)
        super.onStop()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        if (::web.isInitialized) web.saveState(outState)
    }

    override fun onDestroy() {
        if (::web.isInitialized) { (web.parent as? ViewGroup)?.removeView(web); web.destroy() }
        super.onDestroy()
    }

    @Deprecated("pre-33 back handling")
    @Suppress("DEPRECATION")
    override fun onBackPressed() = back()

    private fun back() { if (web.canGoBack()) web.goBack() else moveTaskToBack(true) }

    // ---- views -----------------------------------------------------------------------

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun buildViews() {
        root = FrameLayout(this).apply { clipToPadding = false; background = barsBg }     // the orb's glow may reach under the bars
        web = PasteWebView(this) { bytes, mime -> deliverImage(bytes, mime) }
        offBanner = TextView(this).apply {
            textSize = 16f; gravity = Gravity.CENTER
            setTextColor(Color.WHITE); setBackgroundColor(Color.parseColor("#C62828"))
            minHeight = dp(48); setPadding(dp(16), dp(10), dp(16), dp(10))
            visibility = View.GONE
            setOnClickListener { startListening() }
        }
        val column = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        column.addView(offBanner, LinearLayout.LayoutParams(-1, -2))
        column.addView(web, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(column, FrameLayout.LayoutParams(-1, -1))
        page = column

        // Listening orb: shown while a wake take records, then while it is transcribed. One unit:
        // the disc and, BUTTON_GAP under the disc's edge (not the glow's), 说完了 / 取消.
        orb = ListenOrb(this) { Hub.level }.apply { reserveBelow = dp(BUTTON_GAP + BUTTON_H) }
        fun orbButton(label: String, action: String, primary: Boolean) = TextView(this).apply {
            text = label; textSize = 16f; gravity = Gravity.CENTER
            setTextColor(Color.parseColor(if (primary) "#141414" else "#FAF9F5"))
            if (primary) typeface = android.graphics.Typeface.DEFAULT_BOLD
            minHeight = dp(BUTTON_H); minWidth = dp(112); setPadding(dp(24), 0, dp(24), 0)
            // Opaque, on top of the scrim: 说完了 light on dark, 取消 dark with a light edge.
            background = android.graphics.drawable.GradientDrawable().apply {
                setColor(Color.parseColor(if (primary) "#FAF9F5" else "#141414")); cornerRadius = dp(BUTTON_H / 2).toFloat()
                setStroke(dp(if (primary) 0 else 1), Color.parseColor("#8CFAF9F5"))
            }
            elevation = dp(6).toFloat()
            isClickable = true
            setOnClickListener { WakeService.command(this@MainActivity, action) }
        }
        orbButtons = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER
            addView(orbButton("说完了", WakeService.ACTION_END, true), LinearLayout.LayoutParams(-2, -2).apply { marginEnd = dp(8) })
            addView(orbButton("取消", WakeService.ACTION_CANCEL, false), LinearLayout.LayoutParams(-2, -2).apply { marginStart = dp(8) })
        }
        orbColumn = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_HORIZONTAL
            clipChildren = false
            addView(orb, LinearLayout.LayoutParams(-2, -2))
            addView(orbButtons, LinearLayout.LayoutParams(-2, -2))
        }
        // The disc's size (hence the glow margin to take back) is known only after layout.
        orb.addOnLayoutChangeListener { _, _, _, _, _, _, _, _, _ -> placeOrb() }
        scrim = View(this).apply { setBackgroundColor(scrimColor()) }
        orbLayer = FrameLayout(this).apply {
            // Not clickable: taps outside the orb and its buttons still reach the page.
            visibility = View.GONE
            clipChildren = false
            addView(scrim, FrameLayout.LayoutParams(-1, -1))
            addView(orbColumn, FrameLayout.LayoutParams(-2, -2, Gravity.CENTER))
        }
        root.addView(orbLayer, FrameLayout.LayoutParams(-1, -1))

        // Lock cover: after a wake on a locked phone we never show the conversation until the
        // phone is unlocked; the take waits and is filled in after.
        cover = TextView(this).apply {
            text = "解锁后，刚才说的话会填进输入框\n\n点这里解锁"
            textSize = 18f; gravity = Gravity.CENTER
            setTextColor(Color.WHITE); setBackgroundColor(Color.parseColor("#202124"))
            visibility = View.GONE
            isClickable = true
            isFocusable = true; isFocusableInTouchMode = true
            setOnClickListener { requestUnlock() }
        }
        root.addView(cover, FrameLayout.LayoutParams(-1, -1))
        setContentView(root)

        // Edge-to-edge (forced on Android 15): pad for the system bars and the keyboard ourselves.
        window.setDecorFitsSystemWindows(false)
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            val ime = insets.getInsets(WindowInsets.Type.ime())
            keyboard(insets.isVisible(WindowInsets.Type.ime()))
            val bottom = maxOf(bars.bottom, ime.bottom)
            v.setPadding(bars.left, bars.top, bars.right, bottom)
            insetTop = bars.top; insetBottom = bottom; insetLeft = bars.left; insetRight = bars.right
            barsBg.setLayerHeight(1, bottom); barsBg.invalidateSelf()
            placeOrb()
            WindowInsets.CONSUMED
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        CookieManager.getInstance().setAcceptCookie(true)
        if (BuildConfig.DEBUG) WebView.setWebContentsDebuggingEnabled(true)
        with(web.settings) {
            // Lets the page show its in-app-only ≡ entry (App 状态与设置); nothing else keys on it.
            userAgentString = "$userAgentString AgentJApp/${BuildConfig.VERSION_NAME}"
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false      // 朗读 plays after a tap on the page anyway
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            setSupportMultipleWindows(false)
            javaScriptCanOpenWindowsAutomatically = false
        }
        web.webViewClient = object : WebViewClient() {
            // Only the relay's own origin loads in here; anything else opens in the browser.
            override fun shouldOverrideUrlLoading(view: WebView, req: WebResourceRequest): Boolean {
                val u = req.url.toString()
                if (originOf(u) == origin) return false
                if (req.url.scheme == APP_SCHEME) {
                    if (req.url.host == "status") startActivity(Intent(this@MainActivity, StatusActivity::class.java))
                    return true
                }
                try { startActivity(Intent(Intent.ACTION_VIEW, req.url)) } catch (_: Exception) {}
                return true
            }
            override fun onPageFinished(view: WebView, url: String) {
                deliverPending()
                // Maybe just logged in (a new device cookie): the reply watcher tries again now.
                if (originOf(url) == origin) { CookieManager.getInstance().flush(); ReplyWatcher.kick() }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                main.post { answerPermission(request) }
            }
            override fun onShowFileChooser(view: WebView, cb: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = cb
                if (params.isCaptureEnabled && params.acceptTypes.any { it.startsWith("image/") } && openCamera()) return true
                return try {
                    val i = pickerIntent(params)
                    Log.i(TAG, "file chooser: accept=${params.acceptTypes.joinToString()} -> type=${i.type} (webview: ${params.createIntent().let { w -> "${w.type} ${w.getStringArrayExtra(Intent.EXTRA_MIME_TYPES)?.joinToString()}" }})")
                    @Suppress("DEPRECATION") startActivityForResult(i, REQ_FILE)
                    true
                } catch (e: Exception) {
                    Log.w(TAG, "file chooser: $e"); fileCallback = null
                    toast("打不开文件选择：${e.javaClass.simpleName}"); false
                }
            }
        }
    }

    /**
     * The system picker for <input type=file>. Images keep WebView's own intent (the photo
     * picker). Anything with extensions in accept (the 文件 button: .md .txt .pdf …) opens the
     * file manager unfiltered: WebView turns ".md" into no MIME type at all and the picker then
     * greys out every Markdown file. The relay still refuses a type it does not take, and the
     * page shows that refusal.
     */
    private fun pickerIntent(params: WebChromeClient.FileChooserParams): Intent {
        val multiple = params.mode == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE
        val accept = params.acceptTypes.flatMap { it.split(',') }.map { it.trim() }.filter { it.isNotEmpty() }
        val i = if (accept.any { it.startsWith(".") }) {
            Intent(Intent.ACTION_GET_CONTENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
        } else params.createIntent()
        if (multiple) i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        return i
    }

    // ---- the page's own microphone (hold to talk) -------------------------------------

    /**
     * The page asks for the mic when the owner holds the mic button. Only the relay origin gets it,
     * only audio, and the wake listener lets go of the microphone first; it takes it back once
     * relayNative.busy() says the page is done.
     */
    private fun answerPermission(req: PermissionRequest) {
        val audioOnly = req.resources.all { it == PermissionRequest.RESOURCE_AUDIO_CAPTURE }
        val sameOrigin = originOf(req.origin.toString()) == origin
        val granted = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (!audioOnly || !sameOrigin || !granted) { req.deny(); if (!granted) askPermissions(); return }
        Hub.pausedForPage = true
        val t0 = System.currentTimeMillis()
        fun grantWhenFree() {
            val free = Hub.state == Hub.Listen.PAUSED || Hub.state == Hub.Listen.YIELDED || !Hub.on
            if (free || System.currentTimeMillis() - t0 > 400) {
                req.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
                watchPageMic(0)
            } else main.postDelayed({ grantWhenFree() }, 30)
        }
        grantWhenFree()
    }

    private fun watchPageMic(idle: Int) {
        main.postDelayed({
            web.evaluateJavascript("(function(){try{return !!(window.relayNative&&window.relayNative.busy())}catch(e){return false}})()") { r ->
                val busy = r == "true"
                val n = if (busy) 0 else idle + 1
                if (n >= 2) Hub.pausedForPage = false else watchPageMic(n)
            }
        }, 600)
    }

    // ---- wake → take → page -----------------------------------------------------------

    private fun handleWake(i: Intent?) {
        if (i?.getBooleanExtra(EXTRA_FROM_WAKE, false) != true) return
        i.removeExtra(EXTRA_FROM_WAKE)
        val km = getSystemService(KeyguardManager::class.java)
        if (!km.isKeyguardLocked) return
        // Locked phone: light the screen and ask for the fingerprint / face right away. Over the
        // lock screen only the cover shows — never the conversation, never the keyboard; the
        // take waits until the phone is unlocked.
        Log.i(TAG, "wake on a locked phone: cover + unlock prompt")
        showCover()
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        // Asking before our window has focus is cancelled by the system: onWindowFocusChanged asks.
        wantUnlock = true
        if (hasWindowFocus()) { wantUnlock = false; requestUnlock() }
    }

    private fun showCover() {
        cover.visibility = View.VISIBLE
        web.clearFocus()
        web.isFocusable = false; web.isFocusableInTouchMode = false
        cover.requestFocus()
        window.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN or
            android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        window.insetsController?.hide(WindowInsets.Type.ime())
        getSystemService(android.view.inputmethod.InputMethodManager::class.java)
            ?.hideSoftInputFromWindow(root.windowToken, 0)
    }

    private fun requestUnlock() {
        val km = getSystemService(KeyguardManager::class.java)
        if (!km.isKeyguardLocked) { unlocked(); return }
        km.requestDismissKeyguard(this, object : KeyguardManager.KeyguardDismissCallback() {
            override fun onDismissSucceeded() { Log.i(TAG, "unlock: succeeded"); unlocked() }
            // Cancelled or failed: keep the cover; a tap on it asks again.
            override fun onDismissCancelled() { Log.i(TAG, "unlock: cancelled") }
            override fun onDismissError() { Log.w(TAG, "unlock: error") }
        })
    }

    /** Unlocked some other way (lock screen, side key)? The keyguard can report "locked" for a
     *  moment after resume, so look again for a few seconds before trusting the cover. */
    private fun watchUnlock(n: Int = 0) {
        if (!visible || cover.visibility != View.VISIBLE) return
        if (!getSystemService(KeyguardManager::class.java).isKeyguardLocked) { unlocked(); return }
        if (n < 20) main.postDelayed({ watchUnlock(n + 1) }, 250)
    }

    private fun unlocked() {
        Log.i(TAG, "unlocked: cover off, ${Hub.pendingTakes()} take(s) waiting")
        wantUnlock = false
        setShowWhenLocked(false)
        cover.visibility = View.GONE
        web.isFocusable = true; web.isFocusableInTouchMode = true
        window.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED or
            android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
        deliverPending()
    }

    /** Hand the oldest waiting take to the page; retry until the page is ready (e.g. still loading). */
    private fun deliverPending() {
        if (!::web.isInitialized || delivering || !visible || cover.visibility == View.VISIBLE) return
        val t = Hub.peekTake() ?: return
        if (System.currentTimeMillis() - t.at > TAKE_TTL_MS) {
            Hub.dropTake(t)
            orbHide()
            Log.w(TAG, "take expired undelivered (page=${originOf(web.url ?: "") == origin})")
            toast("刚才那段话没能填进输入框（页面没准备好），请再说一次")
            return
        }
        if (!orbShowing() || orb.phase != ListenOrb.Phase.RECOGNIZING) {
            orbShow(ListenOrb.Phase.RECOGNIZING)
            // Page not ready (still loading, offline): the take keeps waiting, the orb does not.
            main.removeCallbacks(orbGiveUp); main.postDelayed(orbGiveUp, 10_000)
        }
        if (originOf(web.url ?: "") != origin) { retryDeliver(); return }
        delivering = true
        val b64 = Base64.encodeToString(t.wav, Base64.NO_WRAP)
        val js = "(function(){try{return !!(window.relayNative&&window.relayNative.take('$b64',${"%.2f".format(java.util.Locale.ROOT, t.seconds)}))}catch(e){return false}})()"
        web.evaluateJavascript(js) { r ->
            delivering = false
            Log.i(TAG, "deliver take ${"%.1f".format(t.seconds)} s -> $r")
            if (r == "true") { main.removeCallbacks(orbGiveUp); Hub.dropTake(t); watchAsr(++asrWatch); deliverPending() } else retryDeliver()
        }
    }

    private fun retryDeliver() = main.postDelayed({ deliverPending() }, 700)

    /** An image the keyboard inserted (PasteWebView): into the page's tray, the paste path (ADR-047). */
    private fun deliverImage(bytes: ByteArray, mime: String) {
        when {
            bytes.isEmpty() -> { toast("读不出这张图片，再试一次"); return }
            bytes.size > PasteWebView.MAX_BYTES -> { toast("文件太大了，上限 25 MB"); return }
            originOf(web.url ?: "") != origin -> return
        }
        val b64 = Base64.encodeToString(bytes, Base64.NO_WRAP)
        web.evaluateJavascript("(function(){try{return !!(window.relayNative&&window.relayNative.image&&window.relayNative.image('$b64','$mime'))}catch(e){return false}})()") { r ->
            Log.i(TAG, "keyboard image ${bytes.size} B $mime -> $r")
            if (r != "true") toast("图片没能加进附件（页面没准备好），再试一次")
        }
    }

    // ---- state -----------------------------------------------------------------------

    private fun refresh() {
        if (!::orbLayer.isInitialized) return
        val off = when {
            !micGranted() -> "未在监听 · 点此授权麦克风"
            Hub.on -> null
            Prefs.stoppedByUser -> "未在监听 · 点此开始"
            Hub.state == Hub.Listen.ERROR -> "未在监听（${Hub.error ?: "出错"}）· 点此开始"
            else -> null                     // starting by itself right now
        }
        offBanner.text = off ?: ""
        offBanner.visibility = if (off != null && cover.visibility != View.VISIBLE) View.VISIBLE else View.GONE
        Hub.note?.let { n ->
            Hub.note = null
            val end = when (n) { "已取消" -> ListenOrb.Phase.CANCELLED; "没听到说话" -> ListenOrb.Phase.EMPTY; else -> null }
            if (end != null && orbShowing() && orb.phase == ListenOrb.Phase.LISTENING) { captureEnded = true; orbEnd(end) }
            else toast(n)
        }
        orbFollowCapture()
        deliverPending()
    }

    // ---- the listening orb -------------------------------------------------------------

    private fun orbShowing() = orbLayer.visibility == View.VISIBLE && !orbHiding

    /**
     * The disc sits at the true center of the screen, exactly where the overlay orb was — not at
     * the center of the padded area (status bar and navigation differ in height) — with the
     * buttons BUTTON_GAP under the disc's edge. Only if that would put the buttons over the page's
     * input area at the bottom does the whole unit move up (the hand-over then shows a short slide).
     */
    private fun placeOrb() {
        if (!::orbColumn.isInitialized) return
        // A translation, not a margin: this runs inside a layout pass, where a new margin would
        // only take effect on some later layout (1.3.1 dev: the unit sat 41 px too high).
        val gap = dp(BUTTON_GAP) - orb.discInset                // < 0: up into the glow's room
        orbButtons.translationY = gap
        orbColumn.translationX = (insetRight - insetLeft) / 2f
        // Centering the column (orb + buttons) puts the orb half a button row too high; the
        // padding for the bars shifts it too.
        var ty = (insetBottom - insetTop) / 2f + dp(BUTTON_H) / 2f
        val h = root.height
        if (h > 0 && orb.height > 0) {
            val buttonsBottom = h / 2f + orb.height / 2f + gap + dp(BUTTON_H)   // disc centered on the screen
            val limit = h - insetBottom - dp(INPUT_AREA).toFloat()
            // …but never so far that the disc runs into the status bar (landscape: the page then
            // shows under the buttons; the orb stays whole).
            val room = h / 2f - orb.height / 2f + orb.discInset - (insetTop + dp(8))
            if (buttonsBottom > limit) ty -= (buttonsBottom - limit).coerceAtMost(room.coerceAtLeast(0f))
        }
        orbColumn.translationY = ty
    }

    /** The page behind the orb, blurred (API 31+, GPU): the orb is the only thing to look at. */
    private fun blur(on: Boolean) {
        page.setRenderEffect(if (on) android.graphics.RenderEffect.createBlurEffect(
            dp(BLUR).toFloat(), dp(BLUR).toFloat(), android.graphics.Shader.TileMode.CLAMP) else null)
    }

    private fun scrimColor() =
        if ((resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK) ==
            android.content.res.Configuration.UI_MODE_NIGHT_YES) 0x99000000.toInt() else 0x8C000000.toInt()   // 60 % / 55 %

    /** Listening while a wake take records; when it ends with a take, 识别中… until the page is done. */
    private fun orbFollowCapture() {
        if (Hub.state == Hub.Listen.CAPTURING) {
            holdIme(true)
            if (!captureEnded && cover.visibility != View.VISIBLE) orbShow(ListenOrb.Phase.LISTENING)
            return
        }
        holdIme(false)
        captureEnded = false
        if (orbShowing() && orb.phase == ListenOrb.Phase.LISTENING) {
            if (Hub.pendingTakes() > 0) orbShow(ListenOrb.Phase.RECOGNIZING) else orbHide()
        }
    }

    private fun orbShow(p: ListenOrb.Phase, text: String = p.text) {
        main.removeCallbacks(orbHideLater)
        // A new wake (or the orb coming back at all) starts clean: nothing of the last ✕ / ✓ shows.
        val fresh = !orbShowing() || (p == ListenOrb.Phase.LISTENING && orb.phase != ListenOrb.Phase.LISTENING)
        if (fresh) orb.reset(p, text) else orb.set(p, text)
        val buttons = p == ListenOrb.Phase.LISTENING
        orbButtons.animate().cancel()
        orbButtons.animate().alpha(if (buttons) 1f else 0f).setDuration(180).start()
        orbButtons.isEnabled = buttons
        for (i in 0 until orbButtons.childCount) orbButtons.getChildAt(i).isClickable = buttons
        if (orbShowing()) return
        orbHiding = false
        orbButtons.alpha = if (buttons) 1f else 0f
        scrim.animate().cancel()
        orbLayer.visibility = View.VISIBLE
        scrim.alpha = if (orbInstant) 1f else 0f
        if (!orbInstant) scrim.animate().alpha(1f).setDuration(220).start()
        blur(true)
        orb.appear(animate = !orbInstant)
        orbInstant = false
    }

    /** A last word (✓ / ✕ and a line), then fade away. */
    private fun orbEnd(p: ListenOrb.Phase, text: String = p.text) {
        orbShow(p, text)
        main.removeCallbacks(orbHideLater)
        main.postDelayed(orbHideLater, if (p == ListenOrb.Phase.DONE) 900 else 1200)
    }

    private val orbHideLater = Runnable { orbHide() }
    private val orbGiveUp = Runnable { if (orb.phase == ListenOrb.Phase.RECOGNIZING) orbHide() }

    private fun orbHide() {
        main.removeCallbacks(orbHideLater)
        if (!orbShowing()) return
        orbHiding = true
        scrim.animate().cancel()
        scrim.animate().alpha(0f).setDuration(220).start()
        orbButtons.animate().cancel()
        orbButtons.animate().alpha(0f).setDuration(160).start()
        blur(false)
        orb.vanish { orbLayer.visibility = View.GONE; orbHiding = false }
    }

    /**
     * After a take went into the page: follow the page's own transcription (its asrBusy count and
     * the field's length — read only, the page has no hook for us) to say when the words are in.
     * A long take the page turns into a voice attachment never raises asrBusy.
     */
    private fun watchAsr(gen: Int, n: Int = 0, base: Int = -1, busySeen: Boolean = false) {
        if (gen != asrWatch || !visible) return
        web.evaluateJavascript("(function(){try{return asrBusy+','+input.value.length}catch(e){return ''}})()") { r ->
            if (gen != asrWatch) return@evaluateJavascript
            val p = (r ?: "").trim('"').split(',').mapNotNull { it.toIntOrNull() }
            if (p.size != 2) { orbEnd(ListenOrb.Phase.DONE, "已交给输入框"); return@evaluateJavascript }
            val (busy, len) = p
            val b = if (base < 0) len else base
            when {
                busy > 0 && n < 120 -> main.postDelayed({ watchAsr(gen, n + 1, b, true) }, 250)
                busy > 0 -> orbHide()                                  // 30 s: the page shows its own state
                !busySeen && n == 0 -> orbEnd(ListenOrb.Phase.DONE, "已作为语音附件")
                len > b -> orbEnd(ListenOrb.Phase.DONE)
                else -> orbEnd(ListenOrb.Phase.EMPTY, "没识别出文字")      // the page's toast says why
            }
        }
    }

    /** Follow the page's theme-color (it is the state color) under the status bar, and the same
     *  colour under the context-window water (when the water is up) under the gesture bar. */
    private fun tick() {
        if (origin == "https://m.agentj.app" || origin == "https://alpha-web.agentjarvis.net") {
            web.evaluateJavascript("JSON.stringify(window.agentjNative?.snapshot?.()||{})") { result ->
                try {
                    val decoded=org.json.JSONTokener(result).nextValue() as? String ?: return@evaluateJavascript
                    val cfg=org.json.JSONObject(decoded)
                    if(!cfg.optBoolean("ready",false))return@evaluateJavascript
                    Prefs.wakeTokens = cfg.optString("wake_tokens", "")
                    Prefs.wakeEnabled=cfg.optBoolean("wake_enabled",false)
                    Prefs.threshold=cfg.optDouble("wake_threshold",0.25).toFloat()
                    Prefs.speakNotifications=cfg.optBoolean("speak_notifications",false)
                    Prefs.ttsMode=cfg.optString("tts_mode","phone")
                    Prefs.ttsVoice=cfg.optString("tts_voice","")
                    Prefs.ttsRate=cfg.optDouble("tts_rate",1.0).toFloat()
                    Prefs.language=cfg.optString("language","zh")
                } catch (_: Exception) {}
            }
        }

        if (!visible) return
        web.evaluateJavascript("(function(){var m=document.querySelector('meta[name=theme-color]'),w=document.getElementById('water'),a='';" +
            "if(w){var s=getComputedStyle(w),r=w.getBoundingClientRect();if(s.display!=='none'&&r.height>0.5&&r.bottom>=innerHeight-1)a=s.backgroundColor}" +
            "return (m?m.content:'')+'|'+a})()") { r ->
            val (c, water) = (r?.trim('"') ?: "").split('|', limit = 2).let { it[0] to it.getOrElse(1) { "" } }
            try {
                if (c.isNotEmpty()) {
                    val color = Color.parseColor(c)
                    val band = BarBand.over(color, water)
                    if (band != bandColor) {
                        bandColor = band
                        (barsBg.getDrawable(1) as android.graphics.drawable.ColorDrawable).color = band
                    }
                    if (color != themeColor) {
                        themeColor = color
                        (barsBg.getDrawable(0) as android.graphics.drawable.ColorDrawable).color = color
                        val light = Color.luminance(color) > 0.5f
                        window.insetsController?.setSystemBarsAppearance(
                            if (light) WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS else 0,
                            WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS)
                    }
                }
            } catch (_: IllegalArgumentException) {}
        }
        main.postDelayed({ tick() }, 1000)
    }

    /** Keyboard up = the wake listener lets go of the microphone, so Gboard's voice typing works in
     *  here (the owner is typing anyway); keyboard down = it listens again. */
    private fun keyboard(up: Boolean) {
        if (up && imeHeld) {
            // The page's field took focus as the app came forward during a wake take: no keyboard
            // now (it would take the microphone and end the take); the words go in when it is done.
            Log.i(TAG, "keyboard during a wake take: hidden, microphone kept")
            window.insetsController?.hide(WindowInsets.Type.ime())
            return
        }
        val want = up && visible
        if (Hub.keyboardUp != want) { Log.i(TAG, "keyboard ${if (want) "up: microphone free for voice typing" else "down"}"); Hub.keyboardUp = want }
    }

    /**
     * While a wake take records: the window does not bring the keyboard up (its state on resume is
     * "hidden") and the page cannot focus a field. Released as soon as the take ends, before the
     * words are handed to the page, so whatever the page does with focus then is up to it.
     */
    private fun holdIme(on: Boolean) {
        if (imeHeld == on || cover.visibility == View.VISIBLE) return
        imeHeld = on
        Log.i(TAG, if (on) "wake take: keyboard held down" else "wake take over: keyboard free")
        if (on) {
            window.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN or
                android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
            web.clearFocus()
            web.isFocusable = false; web.isFocusableInTouchMode = false
            window.insetsController?.hide(WindowInsets.Type.ime())
        } else {
            web.isFocusable = true; web.isFocusableInTouchMode = true
            window.setSoftInputMode(android.view.WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED or
                android.view.WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)
            root.rootWindowInsets?.let { keyboard(it.isVisible(WindowInsets.Type.ime())) }
        }
    }

    private fun micGranted() = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** On screen and not listening: start, unless the owner stopped it himself (then the banner asks).
     *  The app is in the foreground here, which is exactly when a microphone service may start. */
    private fun startListeningIfWanted() {
        if (!Prefs.stoppedByUser && micGranted() && !Hub.on) WakeService.start(this)
    }

    private fun startListening() {
        Prefs.stoppedByUser = false
        if (!micGranted()) requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_PERMS)
        else WakeService.start(this)
        refresh()
    }

    private fun askPermissions() {
        val want = mutableListOf(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33) want += Manifest.permission.POST_NOTIFICATIONS
        val missing = want.filter { checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED }
        if (missing.isNotEmpty()) requestPermissions(missing.toTypedArray(), REQ_PERMS)
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, perms, results)
        if (code == REQ_PERMS) { startListeningIfWanted(); refresh() }
    }

    @Deprecated("startActivityForResult is enough here; no AndroidX")
    @Suppress("DEPRECATION")
    override fun onActivityResult(code: Int, result: Int, data: Intent?) {
        super.onActivityResult(code, result, data)
        if (code == REQ_CAMERA) { cameraDone(result); return }
        if (code != REQ_FILE) return
        val cb = fileCallback ?: run { Log.w(TAG, "file result with no page waiting (activity recreated?)"); return }
        fileCallback = null
        if (result != RESULT_OK || data == null) { cb.onReceiveValue(null); return }
        val clip: ClipData? = data.clipData
        val picked = if (clip != null) List(clip.itemCount) { clip.getItemAt(it).uri }.filterNotNull()
                     else listOfNotNull(data.data)
        Log.i(TAG, "picked ${picked.size} file(s)")
        // Copy each pick into our own cache first: the picker's content:// grant belongs to the
        // gallery / file manager and can lapse (or be unreadable to the WebView) by the time the
        // page reads the bytes — that failed silently in 1.0. Errors are shown, never swallowed.
        Thread({
            PickProvider.prune(this)
            val out = mutableListOf<Uri>()
            val failed = mutableListOf<String>()
            for (u in picked) {
                try { out += copyPick(u) }
                catch (e: Exception) { Log.w(TAG, "copy pick failed: $e"); failed += (e.message ?: e.javaClass.simpleName) }
            }
            main.post {
                if (failed.isNotEmpty()) toast("有 ${failed.size} 个文件读不出来：${failed.first()}")
                cb.onReceiveValue(if (out.isEmpty()) null else out.toTypedArray())
            }
        }, "jarvis-pick").start()
    }

    /** One picked file → a copy under our PickProvider (same name and type). Big files are cut
     *  one byte past the relay's cap so the page still shows its own "too large" message. */
    private fun copyPick(u: Uri): Uri {
        val cr = contentResolver
        var name = "upload"
        try {
            cr.query(u, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst() && !c.isNull(0)) name = c.getString(0)
            }
        } catch (_: Exception) {}
        val type = cr.getType(u)
        if (!name.contains('.') && type != null) {
            android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(type)?.let { name = "$name.$it" }
        }
        val f = PickProvider.newFile(this, name)
        val input = cr.openInputStream(u) ?: throw java.io.IOException("打不开")
        var n = 0L
        input.use { i -> f.outputStream().use { o ->
            val buf = ByteArray(64 * 1024)
            while (n < PICK_MAX) {
                val r = i.read(buf, 0, minOf(buf.size.toLong(), PICK_MAX - n).toInt())
                if (r < 0) break
                o.write(buf, 0, r); n += r
            }
        } }
        Log.i(TAG, "pick copied: $n bytes, type=$type")
        if (n == 0L) { f.delete(); throw java.io.IOException("文件是空的（还没下载完？）") }
        return PickProvider.uriFor(this, f)
    }

    /** 拍照: the camera app writes straight into our cache through a one-off write grant. */
    private fun openCamera(): Boolean {
        return try {
            PickProvider.prune(this)
            val f = PickProvider.newFile(this, "拍照-${java.text.SimpleDateFormat("MMdd-HHmmss", java.util.Locale.ROOT).format(java.util.Date())}.jpg")
            val uri = PickProvider.uriFor(this, f)
            val i = Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE)
                .putExtra(android.provider.MediaStore.EXTRA_OUTPUT, uri)
                .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            i.clipData = ClipData.newRawUri("", uri)
            cameraFile = f
            @Suppress("DEPRECATION") startActivityForResult(i, REQ_CAMERA)
            true
        } catch (e: Exception) {
            Log.w(TAG, "camera: $e"); cameraFile = null
            false                                   // no camera app: fall back to the picker
        }
    }

    private fun cameraDone(result: Int) {
        val cb = fileCallback ?: return
        fileCallback = null
        val f = cameraFile; cameraFile = null
        if (result == RESULT_OK && f != null && f.length() > 0) cb.onReceiveValue(arrayOf(PickProvider.uriFor(this, f)))
        else {
            f?.delete()
            if (result == RESULT_OK) toast("相机没有交回照片，再拍一次")
            cb.onReceiveValue(null)
        }
    }

    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()
}

/** scheme://host[:port], lower-cased; "" when it is not an http(s) URL. */
fun originOf(url: String): String {
    val u = try { Uri.parse(url) } catch (_: Exception) { return "" }
    val scheme = u.scheme?.lowercase() ?: return ""
    if (scheme != "https" && scheme != "http") return ""
    val host = u.host?.lowercase() ?: return ""
    return if (u.port == -1) "$scheme://$host" else "$scheme://$host:${u.port}"
}
