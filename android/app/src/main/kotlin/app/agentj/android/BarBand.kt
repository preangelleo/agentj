package app.agentj.android

/**
 * The strip under the gesture bar is ours (the page stops above it), so it is painted from the
 * page: its colour (theme-color) with the context-window water composited on top when the water
 * reaches the screen's bottom edge — the same colour the page shows right above the strip.
 * Pure ARGB ints, no android.graphics, so it is unit-tested on the JVM.
 */
object BarBand {
    private val RGBA = Regex("""rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+%?))?\s*\)""")

    /** [css] is getComputedStyle(water).backgroundColor ("rgba(20, 20, 20, 0.13)") or "" for no water. */
    fun over(base: Int, css: String): Int {
        val m = RGBA.find(css) ?: return base
        val (r, g, b) = m.groupValues.subList(1, 4).map { it.toDouble() }
        val aRaw = m.groupValues[4]
        val a = when {
            aRaw.isEmpty() -> 1.0
            aRaw.endsWith("%") -> aRaw.dropLast(1).toDouble() / 100
            else -> aRaw.toDouble()
        }.coerceIn(0.0, 1.0)
        fun mix(c: Int, o: Double) = Math.round(c * (1 - a) + o * a).toInt().coerceIn(0, 255)
        return (0xFF shl 24) or
            (mix((base shr 16) and 0xFF, r) shl 16) or
            (mix((base shr 8) and 0xFF, g) shl 8) or
            mix(base and 0xFF, b)
    }
}
