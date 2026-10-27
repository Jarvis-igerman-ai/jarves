package ai.jarves.companion

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.IOException
import java.net.URL
import java.security.KeyStore
import java.security.cert.CertificateException
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
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
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
        val trust = PinTrust(pin)
        val c = try {
            open(hostPort(url), url.removePrefix("https://").substring(hostPort(url).length), trust)
        } catch (e: RuntimeException) {
            throw IOException("bad address")
        }
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

private const val KEY_ALIAS = "jarves_session_key"

/** One AES-256-GCM key that lives in the Android Keystore (hardware-backed where the phone has it) and never leaves it. */
private fun keystoreKey(): SecretKey {
    val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    (ks.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
    val gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
    gen.init(
        KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build(),
    )
    return gen.generateKey()
}

/**
 * The token lives only as one AES-GCM sealed blob (key in the Keystore) in private preferences, never in a log or a backup (allowBackup is
 * off and dataExtractionRules exclude it). If the key is gone (restore, factory reset, lock-screen change on some phones) the blob cannot
 * be opened: the session is dropped and the owner pairs again.
 */
class SessionStore(private val context: Context) {
    private val sealer = AesGcmSealer(::keystoreKey)

    private fun prefs() = context.getSharedPreferences("jarves_session_v2", Context.MODE_PRIVATE)

    init {
        try {
            context.deleteSharedPreferences("jarves_session")             // the earlier (A2) store: pair again once
        } catch (e: Exception) {
            // nothing to remove
        }
    }

    fun load(): Session? = try {
        prefs().getString("blob", null)?.let { SessionCodec.decode(it, sealer) }
    } catch (e: Exception) {
        null
    }

    fun save(s: Session) {
        try {
            prefs().edit().putString("blob", SessionCodec.encode(s, sealer)).apply()
        } catch (e: Exception) {
            prefs().edit().clear().apply()                                  // never keep a half state: the owner pairs again
        }
    }

    fun clear() {
        prefs().edit().clear().apply()
    }
}
