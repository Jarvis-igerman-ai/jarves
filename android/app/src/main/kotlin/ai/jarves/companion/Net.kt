package ai.jarves.companion

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.io.IOException
import java.net.URL
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

/**
 * The PC's phone link uses a self-signed certificate. The app trusts exactly ONE certificate: the one whose SHA-256 the owner
 * confirmed when pairing (trust on first use, shown to the owner to compare with the computer's screen). Anything else is refused.
 * Capture mode (pin == null) is used only by [probeFingerprint], which sends no request at all.
 */
class PinTrust(private val pin: String?) : X509TrustManager {
    var seen: String? = null
        private set

    override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = throw CertificateException("no client auth")

    override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
        val leaf = chain.firstOrNull()?.encoded ?: throw CertificateException("no certificate")
        seen = Pinning.sha256Hex(leaf)
        if (pin != null && !Pinning.matches(pin, leaf)) throw CertificateException("the PC's certificate changed")
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

private fun open(url: String, trust: PinTrust): HttpsURLConnection {
    val ctx = SSLContext.getInstance("TLS")
    ctx.init(null, arrayOf(trust), null)
    val c = URL(url).openConnection() as HttpsURLConnection
    c.sslSocketFactory = ctx.socketFactory
    c.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true }   // the pinned certificate IS the identity (an IP/LAN name has no valid certificate)
    c.connectTimeout = 6000
    c.readTimeout = 8000
    c.instanceFollowRedirects = false
    return c
}

class PinnedTransport(private val pin: String?) : Transport {
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
        val trust = PinTrust(pin)
        val c = open(url, trust)
        try {
            c.requestMethod = method
            headers.forEach { (k, v) -> c.setRequestProperty(k, v) }
            if (body != null) {
                c.doOutput = true
                c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = c.responseCode
            val stream = if (status >= 400) c.errorStream else c.inputStream
            val text = stream?.use { it.readBytes().take(256 * 1024).toByteArray().toString(Charsets.UTF_8) } ?: ""
            return Response(status, text)
        } catch (e: CertificateException) {
            throw IOException("certificate", e)
        } finally {
            c.disconnect()
        }
    }
}

/** Connects once, reads the certificate fingerprint and sends nothing: the owner compares it with the computer before pairing. */
fun probeFingerprint(base: String): String? {
    val trust = PinTrust(null)
    val c = open("https://$base/", trust)
    try {
        c.connect()
    } catch (e: IOException) {
        if (trust.seen == null) return null
    } finally {
        c.disconnect()
    }
    return trust.seen
}

data class Session(val base: String, val token: String, val pin: String, val device: String)

/** The token lives only in Android's encrypted preferences (Keystore-backed), never in a log or a backup (allowBackup is off). */
class SessionStore(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        context, "jarves_session",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    fun load(): Session? {
        val b = prefs.getString("base", null) ?: return null
        val t = prefs.getString("token", null) ?: return null
        val p = prefs.getString("pin", null) ?: return null
        return Session(b, t, p, prefs.getString("device", "") ?: "")
    }

    fun save(s: Session) {
        prefs.edit().putString("base", s.base).putString("token", s.token).putString("pin", s.pin).putString("device", s.device).apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }
}
