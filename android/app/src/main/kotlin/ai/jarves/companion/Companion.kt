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
}

enum class PairError { BAD_CODE, REFUSED, RATE, UNREACHABLE, BAD_ANSWER }

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

    /** The first call after pairing and then every few seconds: is the PC there, and is its brain running? */
    fun check(token: String): Link {
        val r = try {
            transport.send(
                "GET", url("/api/m/companion/info"),
                mapOf("Authorization" to "Bearer $token", API_HEADER to API_SUPPORTED.toString()), null,
            )
        } catch (e: IOException) {
            return Link.Unreachable
        }
        if (r.status == 401) return Link.Revoked
        if (r.status != 200) return Link.Unreachable
        val info = parseInfo(r.body) ?: return Link.Unreachable
        if (info.apiMin > API_SUPPORTED) return Link.Incompatible(info.apiMin)
        return if (info.pcState == "online") Link.Online(info) else Link.PcOffline(info)
    }
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
