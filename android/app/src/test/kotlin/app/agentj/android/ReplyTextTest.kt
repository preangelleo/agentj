package app.agentj.android

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReplyTextTest {
    @Test fun baseIsTheSecretPathPrefix() {
        val u = "0f1e2d3c-4b5a-4968-8776-655443322110"
        assertEquals("https://relay.example.net/r/$u", ReplyText.base("https://relay.example.net/r/$u/"))
        assertEquals("https://relay.example.net/r/$u", ReplyText.base("https://relay.example.net/r/$u/index.html?x=1"))
        assertEquals("https://h:8443/r/$u", ReplyText.base(" https://h:8443/r/$u "))
        assertNull(ReplyText.base("https://relay.example.net/"))
        assertNull(ReplyText.base("ftp://h/r/$u/"))
        assertNull(ReplyText.base("not a url"))
    }

    @Test fun excerptIsPlainOneLineAndCut() {
        assertEquals("标题 第一段 加粗 链接", ReplyText.excerpt("# 标题\n\n第一段 **加粗** [链接](https://x)"))
        assertEquals("看代码： let a = 1;", ReplyText.excerpt("看代码：\n\n```js\nlet a = 1;\n```"))
        assertEquals("一 二", ReplyText.excerpt("- 一\n- 二"))
        assertEquals("（新回复）", ReplyText.excerpt("  \n```\n```"))
        val long = "字".repeat(200)
        val e = ReplyText.excerpt(long)
        assertEquals(81, e.codePointCount(0, e.length))
        assertTrue(e.endsWith("…"))
        val emoji = "😀".repeat(100)                     // never cut a surrogate pair in half
        val f = ReplyText.excerpt(emoji)
        assertEquals("😀".repeat(80) + "…", f)
    }

    @Test fun backoffDoublesToFiveMinutesWithJitterAndResets() {
        val b = Backoff(rnd = { 0.5 })
        assertEquals(listOf(2_000L, 4_000L, 8_000L, 16_000L), List(4) { b.next() })
        repeat(20) { b.next() }
        assertEquals(300_000L, b.next())
        b.reset()
        assertEquals(2_000L, b.next())
        val lo = Backoff(rnd = { 0.0 }).next(); val hi = Backoff(rnd = { 1.0 }).next()
        assertEquals(1_600L, lo); assertEquals(2_400L, hi)
    }
}
