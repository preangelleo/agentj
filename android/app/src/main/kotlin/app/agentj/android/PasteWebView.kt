package app.agentj.android

import android.content.Context
import android.os.Bundle
import android.util.Log
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import android.view.inputmethod.InputConnectionWrapper
import android.view.inputmethod.InputContentInfo
import android.webkit.WebView

/**
 * The shell's WebView, plus one thing Chromium does not do: take an image from the keyboard.
 *
 * Gboard's clipboard panel (and its "recent screenshot" chip) inserts an image through
 * InputConnection.commitContent, and only into a field whose EditorInfo lists image MIME types.
 * WebView lists none, so Gboard said 「Current application does not support image pasting here」
 * (emulator probe, 2026-09-30). Here every field declares the bridge's image types and a committed
 * image is read and handed to [onImage]; the page decides what to do with it (the composer puts it
 * in the tray, ADR-047). Long-press 「粘贴」 does not come through here: WebView already turns that
 * into a DOM paste event with the file.
 */
class PasteWebView(ctx: Context, private val onImage: (bytes: ByteArray, mime: String) -> Unit) : WebView(ctx) {

    override fun onCreateInputConnection(outAttrs: EditorInfo): InputConnection? {
        val ic = super.onCreateInputConnection(outAttrs) ?: return null
        outAttrs.contentMimeTypes = IMAGE_TYPES
        return object : InputConnectionWrapper(ic, false) {
            override fun commitContent(info: InputContentInfo, flags: Int, opts: Bundle?): Boolean {
                val mime = IMAGE_TYPES.firstOrNull { info.description.hasMimeType(it) } ?: return false
                if (flags and InputConnection.INPUT_CONTENT_GRANT_READ_URI_PERMISSION != 0) {
                    try { info.requestPermission() } catch (e: Exception) { Log.w(TAG, "no read grant: $e"); return false }
                }
                Thread {
                    val bytes = try {
                        context.contentResolver.openInputStream(info.contentUri)?.use { readCapped(it) }
                    } catch (e: Exception) { Log.w(TAG, "read failed: $e"); null }
                    finally { try { info.releasePermission() } catch (_: Exception) {} }
                    post { onImage(bytes ?: ByteArray(0), mime) }
                }.start()
                return true
            }
        }
    }

    companion object {
        private const val TAG = "JarvisPaste"
        /** = inbox.ALLOWED's images (the bridge is the gate; HEIC is left to the page's own checks). */
        val IMAGE_TYPES = arrayOf("image/png", "image/jpeg", "image/webp", "image/gif")
        /** inbox.MAX_BYTES; one byte more marks "too large" without reading the rest. */
        const val MAX_BYTES = 25 * 1024 * 1024

        fun readCapped(s: java.io.InputStream): ByteArray {
            val out = java.io.ByteArrayOutputStream()
            val buf = ByteArray(64 * 1024)
            while (true) {
                val n = s.read(buf); if (n < 0) break
                out.write(buf, 0, n)
                if (out.size() > MAX_BYTES) break
            }
            return out.toByteArray()
        }
    }
}
