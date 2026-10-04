package app.agentj.android

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import android.webkit.MimeTypeMap
import java.io.File

/**
 * Files the page is about to upload, held in the app's own cache: a picked file copied out of
 * the gallery / file manager (its content:// grant belongs to that app and can lapse before the
 * page reads it), and the camera's photo (the camera app writes it here through a one-off write
 * grant). Not exported; only the WebView in this process and a granted camera app ever open it.
 */
class PickProvider : ContentProvider() {

    companion object {
        private const val DIR = "picks"
        private const val KEEP_MS = 24 * 3600_000L

        fun authority(ctx: Context) = "${ctx.packageName}.picks"
        fun dir(ctx: Context) = File(ctx.cacheDir, DIR).apply { mkdirs() }
        fun uriFor(ctx: Context, f: File): Uri =
            Uri.Builder().scheme("content").authority(authority(ctx)).appendPath(f.name).build()

        /** A fresh file name in the picks dir; the display name survives after the prefix. */
        fun newFile(ctx: Context, displayName: String): File {
            val safe = displayName.replace(Regex("[\\\\/:*?\"<>|\\p{Cntrl}]"), "_").take(120).ifEmpty { "upload" }
            return File(dir(ctx), "${System.currentTimeMillis()}-${(1000..9999).random()}-$safe")
        }

        /** Yesterday's copies are never needed again: the bridge has had them for a day. */
        fun prune(ctx: Context) {
            val cut = System.currentTimeMillis() - KEEP_MS
            dir(ctx).listFiles()?.forEach { if (it.lastModified() < cut) it.delete() }
        }

        private fun displayName(f: File) = f.name.split("-", limit = 3).getOrElse(2) { f.name }
    }

    override fun onCreate() = true

    private fun fileOf(uri: Uri): File? {
        val ctx = context ?: return null
        val name = uri.lastPathSegment ?: return null
        val f = File(dir(ctx), name)
        return if (f.parentFile?.canonicalPath == dir(ctx).canonicalPath) f else null
    }

    override fun getType(uri: Uri): String? {
        val ext = fileOf(uri)?.extension?.lowercase() ?: return null
        return when (ext) {
            "md", "markdown" -> "text/markdown"
            "heic" -> "image/heic"
            "heif" -> "image/heif"
            else -> MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext)
        } ?: "application/octet-stream"
    }

    override fun query(uri: Uri, projection: Array<out String>?, sel: String?, args: Array<out String>?, sort: String?): Cursor? {
        val f = fileOf(uri) ?: return null
        val cols = projection ?: arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)
        val row = cols.map { when (it) {
            OpenableColumns.DISPLAY_NAME -> displayName(f)
            OpenableColumns.SIZE -> f.length()
            else -> null
        } }.toTypedArray()
        return MatrixCursor(cols, 1).apply { addRow(row) }
    }

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor? {
        val f = fileOf(uri) ?: throw java.io.FileNotFoundException("not a pick")
        return ParcelFileDescriptor.open(f, ParcelFileDescriptor.parseMode(mode))
    }

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null
    override fun delete(uri: Uri, sel: String?, args: Array<out String>?): Int = 0
    override fun update(uri: Uri, values: ContentValues?, sel: String?, args: Array<out String>?): Int = 0
}
