package ai.jarves.companion

import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.security.MessageDigest

/**
 * The whole contract of the app with the PC, kept free of Android classes so it is unit-tested on the JVM.
 * It speaks ONLY the existing phone API (docs/api/companion_v1.md): pair once, then read /api/m/companion/info.
 * It never queues a write by itself and never talks to anything but the paired PC.
 */
const val API_SUPPORTED = 1
const val API_HEADER = "X-Jarves-Api"

data class Response(val status: Int, val body: String)

interface Transport {
    @Throws(IOException::class)
    fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response

    /** A binary request body (the push-to-talk WAV). A transport that cannot send one says so instead of guessing. */
    @Throws(IOException::class)
    fun sendBytes(method: String, url: String, headers: Map<String, String>, body: ByteArray): Response =
        throw IOException("binary bodies are not supported by this transport")
}

data class Info(
    val api: Int,
    val apiMin: Int,
    val coreVersion: String,
    val pcState: String,
    val serverTime: Double,
    val scopes: List<String>,
)

/** What the owner sees. PcOffline = the PC answered but its Core does not; Unreachable = the PC itself did not answer. */
sealed interface Link {
    data class Online(val info: Info) : Link
    data class PcOffline(val info: Info) : Link
    data object Unreachable : Link
    data object Revoked : Link
    data class Incompatible(val apiMin: Int) : Link

    /** The PC now presents a DIFFERENT certificate than the one the owner confirmed: not "offline" - possibly someone in between. */
    data object CertChanged : Link
}

/** Thrown by the transport when the pinned certificate did not match. */
class CertChangedException(message: String) : IOException(message)

enum class PairError { BAD_ADDRESS, BAD_CODE, REFUSED, RATE, UNREACHABLE, BAD_ANSWER }

class PairFailed(val kind: PairError) : Exception(kind.name)

data class Paired(val device: String, val token: String)

fun parseInfo(body: String): Info? = try {
    val j = JSONObject(body)
    val pc = j.optJSONObject("pc")
    val scopes = j.optJSONArray("scopes")
    Info(
        api = j.getInt("api"),
        apiMin = j.optInt("api_min", 1),
        coreVersion = j.optString("core_version", ""),
        pcState = pc?.optString("state", "") ?: "",
        serverTime = j.optDouble("server_time", 0.0),
        scopes = (0 until (scopes?.length() ?: 0)).map { scopes!!.getString(it) },
    )
} catch (e: JSONException) {
    null
}

class CompanionClient(private val base: String, private val transport: Transport) {
    private fun url(path: String) = "https://$base$path"

    /** One-time 8-digit code from the PC (dashboard, Phone page) -> a device token. */
    fun pair(code: String, name: String): Paired {
        if (!Regex("\\d{8}").matches(code)) throw PairFailed(PairError.BAD_CODE)
        val body = JSONObject().put("code", code).put("name", name.take(40).ifBlank { "Android" }).toString()
        val r = try {
            transport.send("POST", url("/api/m/pair"), mapOf("X-Jarves" to "1", "Content-Type" to "application/json"), body)
        } catch (e: IOException) {
            throw PairFailed(PairError.UNREACHABLE)
        } catch (e: RuntimeException) {
            throw PairFailed(PairError.UNREACHABLE)
        }
        when (r.status) {
            200 -> Unit
            429 -> throw PairFailed(PairError.RATE)
            else -> throw PairFailed(PairError.REFUSED)
        }
        return try {
            val j = JSONObject(r.body)
            if (!j.optBoolean("ok", false)) throw PairFailed(PairError.REFUSED)
            Paired(j.getString("device"), j.getString("token"))
        } catch (e: JSONException) {
            throw PairFailed(PairError.BAD_ANSWER)
        }
    }

    /** One push-to-talk turn: the recording goes to the paired PC only, over the pinned link, and the answer is shown, never spoken or stored. */
    fun voice(token: String, samples: ShortArray): Voice {
        if (samples.size < VOICE_MIN_SAMPLES) return Voice.NoSpeech            // a tap, not a sentence: nothing is sent
        val r = try {
            transport.sendBytes(
                "POST", url("/api/m/voice"),
                mapOf("Authorization" to "Bearer $token", "Content-Type" to "audio/wav", "X-Jarves" to "1", API_HEADER to API_SUPPORTED.toString()),
                Wav.encode(samples.copyOf(minOf(samples.size, VOICE_RATE * VOICE_MAX_SECONDS))),
            )
        } catch (e: IOException) {
            return Voice.Unreachable
        } catch (e: RuntimeException) {
            return Voice.Unreachable
        }
        return when (r.status) {
            200 -> try {
                val j = JSONObject(r.body)
                val t = j.optString("transcript", "")
                if (t.isBlank() && !j.optBoolean("ok", false)) Voice.NoSpeech else Voice.Heard(t, j.optString("reply", ""))
            } catch (e: JSONException) {
                Voice.Unreachable
            }
            401 -> Voice.Revoked
            403 -> Voice.NotAllowed
            409 -> if (codeOf(r.body) == "BUSY") Voice.Busy else Voice.NeedsSecureLink
            429 -> Voice.RateLimited
            else -> Voice.Unreachable
        }
    }

    /** The first call after pairing and then every few seconds: is the PC there, and is its brain running? */
    fun check(token: String): Link {
        val r = try {
            transport.send(
                "GET", url("/api/m/companion/info"),
                mapOf("Authorization" to "Bearer $token", API_HEADER to API_SUPPORTED.toString()), null,
            )
        } catch (e: CertChangedException) {
            return Link.CertChanged
        } catch (e: IOException) {
            return Link.Unreachable
        } catch (e: RuntimeException) {
            return Link.Unreachable                 // a hostile or odd server must never crash the app
        }
        if (r.status == 401) return Link.Revoked
        if (r.status != 200) return Link.Unreachable
        val info = parseInfo(r.body) ?: return Link.Unreachable
        if (info.apiMin > API_SUPPORTED) return Link.Incompatible(info.apiMin)
        return if (info.pcState == "online") Link.Online(info) else Link.PcOffline(info)
    }
}

/** What a push-to-talk turn gives back. Everything that is not [Heard] is a plain reason the app can show; nothing is retried or queued. */
sealed interface Voice {
    data class Heard(val transcript: String, val reply: String) : Voice
    data object NoSpeech : Voice
    data object Busy : Voice
    data object RateLimited : Voice
    data object NeedsSecureLink : Voice
    data object NotAllowed : Voice
    data object Revoked : Voice
    data object Unreachable : Voice
}

/** 16 kHz mono 16-bit: the format the PC's speech recogniser wants, and about 31 KB per second (the PC accepts 2 MB). */
const val VOICE_RATE = 16000
const val VOICE_MAX_SECONDS = 55
const val VOICE_MIN_SAMPLES = VOICE_RATE / 4

object Wav {
    /** A canonical 44-byte header plus the samples, little-endian. */
    fun encode(samples: ShortArray, rate: Int = VOICE_RATE): ByteArray {
        val data = samples.size * 2
        val out = java.nio.ByteBuffer.allocate(44 + data).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        out.put("RIFF".toByteArray(Charsets.US_ASCII)).putInt(36 + data).put("WAVE".toByteArray(Charsets.US_ASCII))
        out.put("fmt ".toByteArray(Charsets.US_ASCII)).putInt(16).putShort(1).putShort(1).putInt(rate).putInt(rate * 2).putShort(2).putShort(16)
        out.put("data".toByteArray(Charsets.US_ASCII)).putInt(data)
        for (s in samples) out.putShort(s)
        return out.array()
    }
}

private fun codeOf(body: String): String = try {
    JSONObject(body).optString("code", "")
} catch (e: JSONException) {
    ""
}

/** Clock skew in seconds between this phone and the PC (the PC's `server_time` minus ours). The app shows it when it is large. */
fun skewSeconds(info: Info, nowSeconds: Double): Double = info.serverTime - nowSeconds

object Pinning {
    fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    /** "ab:cd:..." in groups, the form the owner compares on the PC screen. */
    fun pretty(hex: String): String = hex.chunked(2).joinToString(":")

    fun matches(pinHex: String?, certDer: ByteArray): Boolean =
        pinHex != null && pinHex.equals(sha256Hex(certDer), ignoreCase = true)
}


/** The address the owner types: a host name or IPv4/IPv6 literal and an explicit port, nothing else (no scheme, path, query, user info). */
fun parseBase(input: String): String? {
    val t = input.trim()
    val m = Regex("^(\\[[0-9A-Fa-f:.]{2,45}\\]|[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?):(\\d{1,5})$").matchEntire(t) ?: return null
    val port = m.groupValues[3].toInt()
    return if (port in 1..65535) t else null
}

/** The PC's phone link uses a self-signed certificate: trust exactly the one whose SHA-256 the owner confirmed (null pin = capture mode for the probe only). */
class PinTrust(private val pin: String?) : javax.net.ssl.X509TrustManager {
    var seen: String? = null
        private set

    override fun checkClientTrusted(chain: Array<java.security.cert.X509Certificate>, authType: String) =
        throw java.security.cert.CertificateException("no client auth")

    override fun checkServerTrusted(chain: Array<java.security.cert.X509Certificate>, authType: String) {
        val leaf = chain.firstOrNull()?.encoded ?: throw java.security.cert.CertificateException("no certificate")
        seen = Pinning.sha256Hex(leaf)
        if (pin != null && !Pinning.matches(pin, leaf)) throw java.security.cert.CertificateException("the PC's certificate changed")
    }

    override fun getAcceptedIssuers(): Array<java.security.cert.X509Certificate> = emptyArray()
}

/** Seals and opens bytes. [open] gives null for anything that is not a blob this sealer made with this key (tampered, other key, garbage). */
interface Sealer {
    fun seal(plain: ByteArray): ByteArray
    fun open(boxed: ByteArray): ByteArray?
}

/** AES-GCM with a fresh random 12-byte nonce in front of every blob. The key comes from [key] (the Android Keystore in the app). */
class AesGcmSealer(private val key: () -> javax.crypto.SecretKey) : Sealer {
    override fun seal(plain: ByteArray): ByteArray {
        val c = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding")
        c.init(javax.crypto.Cipher.ENCRYPT_MODE, key())
        return c.iv + c.doFinal(plain)
    }

    override fun open(boxed: ByteArray): ByteArray? {
        if (boxed.size < 12 + 16) return null
        return try {
            val c = javax.crypto.Cipher.getInstance("AES/GCM/NoPadding")
            c.init(javax.crypto.Cipher.DECRYPT_MODE, key(), javax.crypto.spec.GCMParameterSpec(128, boxed, 0, 12))
            c.doFinal(boxed, 12, boxed.size - 12)
        } catch (e: java.security.GeneralSecurityException) {
            null
        }
    }
}

/** The session as one sealed, base64 string. Decoding returns null (never throws) for anything that is not a complete session. */
object SessionCodec {
    fun encode(s: Session, sealer: Sealer): String {
        val j = JSONObject().put("base", s.base).put("token", s.token).put("pin", s.pin).put("device", s.device)
        return java.util.Base64.getEncoder().encodeToString(sealer.seal(j.toString().toByteArray(Charsets.UTF_8)))
    }

    fun decode(text: String, sealer: Sealer): Session? = try {
        val plain = sealer.open(java.util.Base64.getDecoder().decode(text)) ?: return null
        val j = JSONObject(String(plain, Charsets.UTF_8))
        Session(j.getString("base"), j.getString("token"), j.getString("pin"), j.optString("device", ""))
    } catch (e: Exception) {
        null
    }
}
