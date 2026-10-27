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

    fun approvals(token: String): Approvals {
        val r = try {
            transport.send("GET", url("/api/m/approvals"), mapOf("Authorization" to "Bearer $token", API_HEADER to API_SUPPORTED.toString()), null)
        } catch (e: IOException) {
            return Approvals.Unreachable
        } catch (e: RuntimeException) {
            return Approvals.Unreachable
        }
        return when (r.status) {
            200 -> try {
                val j = JSONObject(r.body)
                val arr = j.optJSONArray("pending")
                val list = (0 until (arr?.length() ?: 0)).mapNotNull { i ->
                    val o = arr!!.optJSONObject(i) ?: return@mapNotNull null
                    val id = o.optString("id", "")
                    if (!Regex("[0-9a-f]{16}").matches(id)) return@mapNotNull null
                    val on = j.optBoolean("phone_approvals", false)
                    Approval(
                        id, o.optString("title", "").take(300), o.optString("kind", ""), o.optString("risk", ""),
                        o.optInt("seconds_left", 0), on && !o.optBoolean("typed", false) && !o.optBoolean("pc_only", false),
                    )
                }
                Approvals.Listed(list, j.optBoolean("phone_approvals", false))
            } catch (e: JSONException) {
                Approvals.Unreachable
            }
            401 -> Approvals.Revoked
            403 -> Approvals.NotAllowed
            else -> Approvals.Unreachable
        }
    }

    /** Approve or deny one approval. The PC decides what is allowed; the app only reports the plain reason it got. */
    fun decide(token: String, id: String, approve: Boolean, commandId: String = newCommandId()): Decision {
        if (!Regex("[0-9a-f]{16}").matches(id)) return Decision.GONE
        val r = try {
            transport.send(
                "POST", url("/api/m/approvals/$id"),
                mapOf("Authorization" to "Bearer $token", "Content-Type" to "application/json", "X-Jarves" to "1", API_HEADER to API_SUPPORTED.toString()),
                JSONObject().put("approved", approve).put("id", commandId).toString(),
            )
        } catch (e: IOException) {
            return Decision.UNREACHABLE
        } catch (e: RuntimeException) {
            return Decision.UNREACHABLE
        }
        return when (r.status) {
            200 -> Decision.DONE
            401 -> Decision.REVOKED
            403 -> when (errCode(r.body)) {
                "PHONE_APPROVALS_OFF" -> Decision.OFF
                "TYPED_PC_ONLY", "PC_ONLY" -> Decision.PC_ONLY
                else -> Decision.NOT_ALLOWED
            }
            409, 404 -> Decision.GONE
            429 -> Decision.RATE
            else -> Decision.UNREACHABLE
        }
    }

    /** Share-to-JARVES: one chat turn with the text the owner confirmed. */
    fun share(token: String, text: String, commandId: String = newCommandId()): Shared {
        val t = cleanShared(text)
        if (t.isEmpty()) return Shared.Answer("")
        val r = try {
            transport.send(
                "POST", url("/api/m/chat"),
                mapOf("Authorization" to "Bearer $token", "Content-Type" to "application/json", "X-Jarves" to "1", API_HEADER to API_SUPPORTED.toString()),
                JSONObject().put("text", t).put("id", commandId).toString(),
            )
        } catch (e: IOException) {
            return Shared.Unreachable
        } catch (e: RuntimeException) {
            return Shared.Unreachable
        }
        return when (r.status) {
            200 -> try {
                Shared.Answer(JSONObject(r.body).optString("reply", ""))
            } catch (e: JSONException) {
                Shared.Unreachable
            }
            401 -> Shared.Revoked
            403 -> Shared.NotAllowed
            409 -> Shared.Busy
            429 -> Shared.RateLimited
            else -> Shared.Unreachable
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

/** One waiting approval as the PC lists it. [phoneCan] is false for typed-code and PC-only ones: the phone may only deny those. */
data class Approval(val id: String, val title: String, val kind: String, val risk: String, val secondsLeft: Int, val phoneCan: Boolean)

sealed interface Approvals {
    data class Listed(val pending: List<Approval>, val phoneApprovalsOn: Boolean) : Approvals
    data object NotAllowed : Approvals
    data object Revoked : Approvals
    data object Unreachable : Approvals
}

enum class Decision { DONE, OFF, PC_ONLY, GONE, RATE, NOT_ALLOWED, REVOKED, UNREACHABLE }

/** One command id per tap, so a resend after a bad connection can never decide twice (the PC replays the first answer). */
fun newCommandId(): String = "app-" + java.util.UUID.randomUUID().toString().replace("-", "").take(24)

/** What sharing text to JARVES gives back. The text is sent only after the owner pressed Send on the app's own screen. */
sealed interface Shared {
    data class Answer(val reply: String) : Shared
    data object Busy : Shared
    data object RateLimited : Shared
    data object NotAllowed : Shared
    data object Revoked : Shared
    data object Unreachable : Shared
}

const val SHARE_MAX_CHARS = 4000

private fun errCode(body: String): String = try {
    JSONObject(body).optString("code", "")
} catch (e: JSONException) {
    ""
}

/** Text from another app's Share sheet, trimmed and cut: nothing else (no streams, no files, no links followed). */
fun cleanShared(raw: CharSequence?): String = raw?.toString()?.replace("\u0000", "")?.trim()?.take(SHARE_MAX_CHARS) ?: ""

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
