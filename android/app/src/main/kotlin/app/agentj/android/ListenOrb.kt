package app.agentj.android

import android.annotation.SuppressLint
import android.content.Context
import android.animation.ObjectAnimator
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.os.SystemClock
import android.view.View
import android.view.animation.DecelerateInterpolator
import android.view.animation.OvershootInterpolator
import kotlin.math.PI
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * The round "Agent J is listening" indicator shown in the middle of the screen after a wake —
 * in the app (MainActivity) and as the overlay window that brings the app forward (Popup).
 *
 * A dark, opaque disc with a soft shadow and a glow in the phase's color. While listening the
 * glow breathes, rings ripple outwards and both swell with the microphone level ([level], 0..1,
 * read every frame). Inside: a small Agent J wordmark, an icon (mic / spinner / check / cross)
 * and one line of status. A phase change fades the old icon and text out completely before the
 * new ones fade in, so the disc never shows two states at once; a new wake starts clean ([reset]).
 * Sizes itself from what its parent offers, so it stays round and centered on any screen,
 * folded or unfolded, portrait or landscape.
 */
@SuppressLint("ViewConstructor")            // built in code only, never inflated
class ListenOrb(ctx: Context, private val level: () -> Float) : View(ctx) {

    enum class Phase(val text: String, val accent: Int) {
        LISTENING("在听…", 0xFF7FB0E0.toInt()),         // windstorm, lifted for glow
        RECOGNIZING("识别中…", 0xFF9C96DA.toInt()),     // magic carpet
        DONE("已填进输入框", 0xFF74B3A0.toInt()),        // emerald oasis
        CANCELLED("已取消", 0xFF9A9A9A.toInt()),        // argent
        EMPTY("没听到说话", 0xFF9A9A9A.toInt()),
        READY("Agent J 在听", 0xFF7FB0E0.toInt()),       // after a restart: listening for the wake word
    }

    var phase = Phase.LISTENING; private set
    /** Room under the orb the parent keeps for other things (MainActivity's two buttons). */
    var reserveBelow = 0

    private val d = resources.displayMetrics.density
    private var dark = night()
    private val ink = Color.parseColor("#FAF9F5")                  // lotion

    private var prev = phase
    private var text = phase.text
    private var prevText = text
    private var changedAt = 0L
    private var smooth = 0f
    private val born = SystemClock.uptimeMillis()

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG)
    private val shade = Paint(Paint.ANTI_ALIAS_FLAG)
    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND }
    private val solid = Paint(Paint.ANTI_ALIAS_FLAG)
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply { textAlign = Paint.Align.CENTER }
    private val mark = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER; typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD); letterSpacing = 0.38f
    }
    private val glowMatrix = Matrix()
    private var glowColor = 0
    private val rect = RectF()
    private val path = Path()

    private var r = 0f                 // disc radius
    private var cx = 0f
    private var cy = 0f

    init {
        contentDescription = "Agent J ${phase.text}"
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_YES
    }

    // ---- public ----------------------------------------------------------------------

    /** Change phase (and optionally the line of text): the old one fades out, then the new one in. */
    fun set(p: Phase, t: String = p.text) {
        if (p == phase && t == text) return
        // Changed again before the new state showed at all: the old one is still what is on screen.
        if (inAlpha(now()) > 0f) { prev = phase; prevText = text }
        phase = p; text = t
        changedAt = now()
        contentDescription = "Agent J $t"
        announceForAccessibility(t)
        invalidate()
    }

    /** Show [p] at once, with nothing left over from the last time (a new wake). */
    fun reset(p: Phase, t: String = p.text) {
        prev = p; prevText = t; phase = p; text = t
        changedAt = 0L; smooth = 0f
        contentDescription = "Agent J $t"
        invalidate()
    }

    private var fadeIn: ObjectAnimator? = null

    /** Grow in (or straight in when [animate] is false: taking over from the overlay). The disc is
     *  opaque within ~90 ms; the scale carries the entrance. */
    fun appear(animate: Boolean = true) {
        animate().cancel(); fadeIn?.cancel()
        if (!animate) { alpha = 1f; scaleX = 1f; scaleY = 1f; return }
        alpha = 0f; scaleX = 0.82f; scaleY = 0.82f
        fadeIn = ObjectAnimator.ofFloat(this, ALPHA, 0f, 1f).setDuration(90).also { it.start() }
        animate().scaleX(1f).scaleY(1f).setDuration(280).setInterpolator(OvershootInterpolator(1.1f)).start()
    }

    fun vanish(done: () -> Unit) {
        animate().cancel(); fadeIn?.cancel()
        animate().alpha(0f).scaleX(0.9f).scaleY(0.9f).setDuration(200).setInterpolator(DecelerateInterpolator())
            .withEndAction(done).start()
    }

    // ---- layout ----------------------------------------------------------------------

    companion object {
        /** A phase change: the old state fades out in the first 45 %, the new one in after it. */
        const val SWAP_MS = 360L
        private const val OUT = 0.45f

        /** Side of the (square) orb for w × h pixels: 240 dp on a phone, never more than ~62 % of
         *  the short side (outer screen, landscape). */
        fun side(density: Float, w: Int, h: Int): Int =
            min(240 * density, 0.62f * min(w, max(h, 0)).toFloat()).coerceAtLeast(120 * density).toInt()
    }

    override fun onMeasure(wSpec: Int, hSpec: Int) {
        // The overlay window is already sized with side(): take it as it is.
        if (MeasureSpec.getMode(wSpec) == MeasureSpec.EXACTLY && MeasureSpec.getMode(hSpec) == MeasureSpec.EXACTLY) {
            val e = min(MeasureSpec.getSize(wSpec), MeasureSpec.getSize(hSpec)); setMeasuredDimension(e, e); return
        }
        val dm = resources.displayMetrics
        fun avail(spec: Int, fallback: Int) =
            if (MeasureSpec.getMode(spec) == MeasureSpec.UNSPECIFIED) fallback else MeasureSpec.getSize(spec)
        val s = side(d, avail(wSpec, dm.widthPixels), avail(hSpec, dm.heightPixels) - reserveBelow)
        setMeasuredDimension(s, s)
    }

    override fun onSizeChanged(w: Int, h: Int, ow: Int, oh: Int) {
        cx = w / 2f; cy = h / 2f
        r = min(w, h) / 2f / 1.36f
        fill.shader = RadialGradient(cx, cy - r * 0.45f, r * 1.55f,
            if (dark) intArrayOf(0xFF303136.toInt(), 0xFF141414.toInt()) else intArrayOf(0xFF403F3A.toInt(), 0xFF2C2B26.toInt()),
            null, Shader.TileMode.CLAMP)
        shade.shader = RadialGradient(cx, cy + r * 0.07f, r * 1.3f,
            intArrayOf(Color.argb(if (dark) 150 else 80, 0, 0, 0), Color.argb(if (dark) 150 else 80, 0, 0, 0), 0),
            floatArrayOf(0f, 0.72f, 1f), Shader.TileMode.CLAMP)
        glowColor = 0
        label.textSize = r * 0.19f
        mark.textSize = r * 0.125f
    }

    /** Distance from the view's edge to the disc's edge (the room the glow takes), in pixels. */
    val discInset get() = if (r > 0f) (min(width, height) / 2f - r) else 0f

    private fun now() = SystemClock.uptimeMillis()
    private fun progress(at: Long) = ((at - changedAt).toFloat() / SWAP_MS).coerceIn(0f, 1f)
    private fun outAlpha(at: Long) = 1f - ease((progress(at) / OUT).coerceIn(0f, 1f))
    private fun inAlpha(at: Long) = ease(((progress(at) - OUT) / (1f - OUT)).coerceIn(0f, 1f))

    private fun night() = (resources.configuration.uiMode and Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES

    override fun onConfigurationChanged(c: Configuration) {
        super.onConfigurationChanged(c)
        dark = night()
        if (width > 0) onSizeChanged(width, height, width, height)
        requestLayout()
    }

    // ---- drawing ---------------------------------------------------------------------

    override fun onDraw(c: Canvas) {
        if (r <= 0f) return
        val now = now()
        val t = ease(progress(now))                 // color: one smooth blend over the whole swap
        val aOut = outAlpha(now)
        val aIn = inAlpha(now)
        val live = phase == Phase.LISTENING
        // Attack fast, release slow: the glow jumps with a syllable and settles between words.
        val target = if (live) level().coerceIn(0f, 1f) else 0f
        smooth += (target - smooth) * (if (target > smooth) 0.45f else 0.12f)

        val accent = blend(prev.accent, phase.accent, t)
        val breath = 0.5f + 0.5f * sin(2 * PI * (now - born) / (if (live) 2400.0 else 1600.0)).toFloat()

        // Drawn past the gradient's end (clamped to transparent): an edge at alpha ≈ 0 still showed
        // as a dotted ring on the phone-like backgrounds.
        c.drawCircle(cx, cy + r * 0.07f, r * 1.4f, shade)

        // Glow: one gradient built per color, scaled per frame.
        if (accent != glowColor) {
            glowColor = accent
            glow.shader = RadialGradient(cx, cy, r * 1.36f,
                intArrayOf(withAlpha(accent, 150), withAlpha(accent, 150), withAlpha(accent, 50), 0),
                floatArrayOf(0f, 0.66f, 0.83f, 1f), Shader.TileMode.CLAMP)
        }
        val grow = if (phase == Phase.CANCELLED || phase == Phase.EMPTY) 0.88f else 0.86f + 0.05f * breath + 0.09f * smooth
        glowMatrix.setScale(grow, grow, cx, cy)
        glow.shader.setLocalMatrix(glowMatrix)
        glow.alpha = ((if (dark) 255 else 215) * (0.75f + 0.25f * smooth)).toInt()
        c.drawCircle(cx, cy, r * 1.36f, glow)                  // the gradient ends at 1.36 r × grow

        // Ripples while listening: two rings, staggered, louder = brighter.
        if (live) for (k in 0..1) {
            val p = (((now - born) + k * 1100L) % 2200L) / 2200f
            line.strokeWidth = 1.6f * d
            line.color = withAlpha(accent, ((1f - p) * (1f - p) * (90 + 120 * smooth) * aIn).toInt())   // gone before the edge
            c.drawCircle(cx, cy, r * (1f + 0.34f * p), line)
        }

        c.drawCircle(cx, cy, r, fill)
        line.strokeWidth = 1f * d
        line.color = Color.argb(if (dark) 40 else 30, 255, 255, 255)
        c.drawCircle(cx, cy, r - 0.5f * d, line)
        // Inner level ring in the accent color.
        line.strokeWidth = 2.2f * d
        line.color = withAlpha(accent, (70 + 150 * smooth).toInt())
        c.drawCircle(cx, cy, r - 7 * d, line)

        mark.color = withAlpha(ink, 120)
        c.drawText("Agent J", cx + mark.letterSpacing * mark.textSize / 2, cy - r * 0.50f, mark)

        val iy = cy - r * 0.06f
        // Never both: the old icon and line are gone (aOut = 0) before the new ones start (aIn > 0).
        if (aOut > 0f) icon(c, prev, iy, aOut, 0.85f + 0.15f * aOut, now)
        if (aIn > 0f) icon(c, phase, iy, aIn, 0.85f + 0.15f * aIn, now)

        val ty = cy + r * 0.56f
        if (aOut > 0f) { label.color = withAlpha(ink, (230 * aOut).toInt()); c.drawText(fit(prevText), cx, ty - 6 * d * (1f - aOut), label) }
        if (aIn > 0f) { label.color = withAlpha(ink, (230 * aIn).toInt()); c.drawText(fit(text), cx, ty + 6 * d * (1f - aIn), label) }

        if (isAttachedToWindow && visibility == VISIBLE) postInvalidateOnAnimation()
    }

    private fun fit(s: String): String {
        val max = r * 1.6f
        if (label.measureText(s) <= max) return s
        var e = s
        while (e.length > 1 && label.measureText("$e…") > max) e = e.dropLast(1)
        return "$e…"
    }

    private fun icon(c: Canvas, p: Phase, y: Float, a: Float, scale: Float, now: Long) {
        if (a <= 0.01f) return
        val s = r * 0.36f * scale * (if (p == Phase.LISTENING) 1f + 0.10f * smooth else 1f)
        val alpha = (255 * a).toInt()
        line.color = withAlpha(ink, alpha); solid.color = withAlpha(ink, alpha)
        line.strokeWidth = s * 0.13f
        when (p) {
            Phase.LISTENING, Phase.READY -> {                       // microphone
                rect.set(cx - s * 0.26f, y - s * 0.78f, cx + s * 0.26f, y + s * 0.10f)
                c.drawRoundRect(rect, s * 0.26f, s * 0.26f, solid)
                rect.set(cx - s * 0.48f, y - s * 0.50f, cx + s * 0.48f, y + s * 0.36f)
                c.drawArc(rect, 20f, 140f, false, line)
                c.drawLine(cx, y + s * 0.37f, cx, y + s * 0.62f, line)
                c.drawLine(cx - s * 0.24f, y + s * 0.64f, cx + s * 0.24f, y + s * 0.64f, line)
            }
            Phase.RECOGNIZING -> {                                   // spinner
                val rot = (now % 1100L) / 1100f * 360f
                val sweep = 200f + 70f * sin(2 * PI * (now % 1600L) / 1600.0).toFloat()
                rect.set(cx - s * 0.5f, y - s * 0.58f, cx + s * 0.5f, y + s * 0.42f)
                line.color = withAlpha(ink, alpha / 5); c.drawArc(rect, 0f, 360f, false, line)
                line.color = withAlpha(ink, alpha); c.drawArc(rect, rot, sweep, false, line)
            }
            Phase.DONE -> {                                          // check
                path.reset()
                path.moveTo(cx - s * 0.44f, y - s * 0.06f)
                path.lineTo(cx - s * 0.12f, y + s * 0.26f)
                path.lineTo(cx + s * 0.46f, y - s * 0.40f)
                c.drawPath(path, line)
            }
            Phase.CANCELLED, Phase.EMPTY -> {                        // cross
                val k = s * 0.34f
                c.drawLine(cx - k, y - k - s * 0.08f, cx + k, y + k - s * 0.08f, line)
                c.drawLine(cx + k, y - k - s * 0.08f, cx - k, y + k - s * 0.08f, line)
            }
        }
    }

    private fun ease(x: Float) = 1f - (1f - x) * (1f - x) * (1f - x)
    private fun withAlpha(c: Int, a: Int) = (c and 0x00FFFFFF) or (a.coerceIn(0, 255) shl 24)
    private fun blend(a: Int, b: Int, t: Float): Int {
        fun ch(sh: Int) = ((a shr sh and 0xFF) + ((b shr sh and 0xFF) - (a shr sh and 0xFF)) * t).toInt()
        return Color.argb(255, ch(16), ch(8), ch(0))
    }
}
