package app.agentj.android

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** R-0051 W9: off-origin navigations handed to the system are web pages and mail only. */
class ExternalLinkTest {
    @Test fun webPagesAndMailGoToTheSystem() {
        for (u in listOf("https://example.com/a?b=c#d", "HTTPS://Example.com", "http://example.com/", "https://auth.openai.com/codex/device",
                         "mailto:feedback@example.com", "https://user@example.com:8443/p", "https://[2001:db8::1]:443/", "MAILTO:someone@example.com?subject=x"))
            assertTrue(u, externalAllowed(u))
    }

    @Test fun everythingElseIsDropped() {
        for (u in listOf("intent://scan/#Intent;scheme=zxing;package=com.example;end", "market://details?id=com.example",
                         "javascript:alert(1)", "data:text/html,<b>x</b>", "file:///sdcard/x", "content://com.example/x",
                         "tel:+15555550100", "sms:+15555550100", "weixin://dl/scan", "tg://resolve?domain=x", "agentj-app://status",
                         "https:/example.com", "https:example.com", "https:///example.com", "https://", "http:", "mailto:", "https://@", "https://:443/path", "http://user@:80/", "https://user:pw@/x",
                         "https://[]/x", "https://?q=1", "https://#frag",
                         ":https://example.com", "", "example.com", " https://example.com", "https:\\\\example.com"))
            assertFalse(u, externalAllowed(u))
    }
}
