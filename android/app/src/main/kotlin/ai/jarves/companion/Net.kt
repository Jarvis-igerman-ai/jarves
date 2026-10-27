package ai.jarves.companion

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.io.IOException
import java.net.URL
import java.security.cert.CertificateException
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLException

/**
 * [PinTrust] (Companion.kt) trusts exactly ONE certificate: the one whose SHA-256 the owner confirmed when pairing. The hostname verifier
 * below accepts any name, so it is safe ONLY because of that pin: never use it with `pin == null` except in [probeFingerprint], which
 * sends no request at all (a handshake only).
 */
private fun open(base: String, path: String, trust: PinTrust): HttpsURLConnection {
    val ctx = SSLContext.getInstance("TLS")
    ctx.init(null, arrayOf(trust), null)
    val c = URL("https://$base$path").openConnection() as HttpsURLConnection
    c.sslSocketFactory = ctx.socketFactory
    c.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }
    c.connectTimeout = 6000
    c.readTimeout = 8000
    c.instanceFollowRedirects = false
    return c
}

private fun hostPort(url: String) = url.removePrefix("https://").substringBefore('/')

private fun pinMismatch(e: Throwable): Boolean {
    var x: Throwable? = e
    while (x != null) {
        if (x is CertificateException) return true
        x = x.cause
    }
    return false
}

class PinnedTransport(private val pin: String?) : Transport {
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response =
        exchange(method, url, headers, body?.toByteArray(Charsets.UTF_8), 8000)

    /** The PC transcribes and answers before it replies, so a voice turn may wait longer than a status poll. */
    override fun sendBytes(method: String, url: String, headers: Map<String, String>, body: ByteArray): Response =
        exchange(method, url, headers, body, 60000)

    private fun exchange(method: String, url: String, headers: Map<String, String>, body: ByteArray?, readMs: Int): Response {
        val trust = PinTrust(pin)
        val c = try {
            open(hostPort(url), url.removePrefix("https://").substring(hostPort(url).length), trust)
        } catch (e: RuntimeException) {
            throw IOException("bad address")
        }
        try {
            c.readTimeout = readMs
            c.requestMethod = method
            headers.forEach { (k, v) -> c.setRequestProperty(k, v) }
            if (body != null) {
                c.doOutput = true
                c.outputStream.use { it.write(body) }
            }
            val status = c.responseCode
            val stream = if (status >= 400) c.errorStream else c.inputStream
            val text = stream?.use { it.readBytes().take(256 * 1024).toByteArray().toString(Charsets.UTF_8) } ?: ""
            return Response(status, text)
        } catch (e: SSLException) {
            // The handshake failed. A pin that no longer matches is NOT "offline": say so (the PC regenerated its certificate, or someone is in between).
            if (pin != null && pinMismatch(e)) throw CertChangedException("certificate")
            throw IOException("tls")
        } catch (e: RuntimeException) {
            throw IOException("bad answer")
        } finally {
            c.disconnect()
        }
    }
}

/** Connects once, reads the certificate fingerprint and sends nothing: the owner compares it with the computer's Phone page before pairing. */
fun probeFingerprint(base: String): String? {
    val trust = PinTrust(null)
    try {
        val c = open(base, "/", trust)
        try {
            c.connect()
        } finally {
            c.disconnect()
        }
    } catch (e: IOException) {
        if (trust.seen == null) return null
    } catch (e: RuntimeException) {
        return null
    }
    return trust.seen
}

data class Session(val base: String, val token: String, val pin: String, val device: String)

/** The token lives only in Android's encrypted preferences (Keystore-backed), never in a log or a backup (allowBackup is off). */
class SessionStore(private val context: Context) {
    private fun prefs() = EncryptedSharedPreferences.create(
        context, "jarves_session",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    /** The library (a deprecated alpha) can fail on some devices or after a restore: then the session is dropped and the owner pairs again. */
    private fun safe(): android.content.SharedPreferences? = try {
        prefs()
    } catch (e: Exception) {
        context.deleteSharedPreferences("jarves_session")
        try {
            prefs()
        } catch (e2: Exception) {
            null
        }
    }

    fun load(): Session? = try {
        val p = safe()
        val b = p?.getString("base", null)
        val t = p?.getString("token", null)
        val pin = p?.getString("pin", null)
        if (b == null || t == null || pin == null) null else Session(b, t, pin, p.getString("device", "") ?: "")
    } catch (e: Exception) {
        null
    }

    fun save(s: Session) {
        safe()?.edit()?.putString("base", s.base)?.putString("token", s.token)?.putString("pin", s.pin)?.putString("device", s.device)?.apply()
    }

    fun clear() {
        safe()?.edit()?.clear()?.apply()
    }
}
