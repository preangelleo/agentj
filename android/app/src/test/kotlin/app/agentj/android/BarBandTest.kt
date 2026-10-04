package app.agentj.android

import org.junit.Assert.assertEquals
import org.junit.Test

class BarBandTest {
    private val water = "rgba(20, 20, 20, 0.13)"

    @Test fun idleGreenUnderWaterMatchesReferenceScreenshot() {
        // OPPO 1.3.1 screenshot (JPEG): strip = page (98,152,136); right above it (88,134,121). ±1 for JPEG.
        val c = BarBand.over(0xFF629888.toInt(), water)
        for ((got, want) in listOf((c shr 16) and 0xFF to 88, (c shr 8) and 0xFF to 134, c and 0xFF to 121))
            assert(Math.abs(got - want) <= 1) { "$got vs $want" }
    }

    @Test fun eachStateColour() {
        val want = mapOf(
            0xFF629887 to 0xFF588778,   // idle · emerald oasis
            0xFF6A9BCC to 0xFF5F89B4,   // working · windstorm
            0xFFD97757 to 0xFFBF6A4E,   // waiting · peachy feeling
            0xFF827DBD to 0xFF746FA7,   // question · magic carpet
            0xFF858585 to 0xFF767676,   // offline · black mana
        )
        for ((bg, out) in want) assertEquals(Integer.toHexString(bg.toInt()), out.toInt(), BarBand.over(bg.toInt(), water))
    }

    @Test fun noWaterLeavesThePageColour() {
        assertEquals(0xFF629887.toInt(), BarBand.over(0xFF629887.toInt(), ""))
        assertEquals(0xFF629887.toInt(), BarBand.over(0xFF629887.toInt(), "rgba(0, 0, 0, 0)"))
    }

    @Test fun otherSpellings() {
        assertEquals(0xFF000000.toInt(), BarBand.over(0xFFFFFFFF.toInt(), "rgb(0, 0, 0)"))
        assertEquals(BarBand.over(0xFF629887.toInt(), water), BarBand.over(0xFF629887.toInt(), "rgb(20 20 20 / 13%)"))
    }
}
