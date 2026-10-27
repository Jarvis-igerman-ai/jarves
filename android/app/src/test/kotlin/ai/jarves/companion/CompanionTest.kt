package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

private class Fake(val answer: (String, String, Map<String, String>, String?) -> Response) : Transport {
    val calls = mutableListOf<List<Any?>>()
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
        calls += listOf(method, url, headers, body)
        return answer(method, url, headers, body)
    }
}

private const val INFO_ONLINE = """{"api":1,"api_min":1,"core_version":"4.5.1","server_time":1793000000.5,
 "pc":{"state":"online","core":"running"},"scopes_supported":["chat"],"scopes":["chat","status"],"features":{}}"""
private const val INFO_OFFLINE = """{"api":1,"api_min":1,"core_version":"4.5.1","server_time":1.0,"pc":{"state":"offline","core":"stopped"},"scopes":["chat"]}"""

class CompanionTest {
    @Test
    fun infoIsParsedFromTheDocumentedShapeAndUnknownFieldsAreIgnored() {
        val i = parseInfo(INFO_ONLINE)!!
        assertEquals(1, i.api)
        assertEquals("online", i.pcState)
        assertEquals("4.5.1", i.coreVersion)
        assertEquals(listOf("chat", "status"), i.scopes)
        assertNull(parseInfo("not json"))
        assertNull(parseInfo("""{"pc":{}}"""))
    }

    @Test
    fun onlinePcOfflineUnreachableRevokedAndIncompatibleAreTold() {
        fun link(r: () -> Response) = CompanionClient("pc:8765", Fake { _, _, _, _ -> r() }).check("tok")
        assertTrue(link { Response(200, INFO_ONLINE) } is Link.Online)
        assertTrue(link { Response(200, INFO_OFFLINE) } is Link.PcOffline)
        assertEquals(Link.Revoked, link { Response(401, "{}") })
        assertEquals(Link.Unreachable, link { Response(503, "{}") })
        assertEquals(Link.Unreachable, link { Response(200, "garbage") })
        assertEquals(Link.Unreachable, CompanionClient("pc:8765", Fake { _, _, _, _ -> throw IOException("down") }).check("tok"))
        val newer = INFO_ONLINE.replace("\"api_min\":1", "\"api_min\":2")
        assertEquals(Link.Incompatible(2), link { Response(200, newer) })
    }

    @Test
    fun theInfoCallSendsTheBearerTokenAndTheApiHeaderOverHttpsToTheOneAddress() {
        val f = Fake { _, _, _, _ -> Response(200, INFO_ONLINE) }
        CompanionClient("192.168.1.5:8766", f).check("secret-token")
        val (method, url, headers, body) = f.calls.single()
        assertEquals("GET", method)
        assertEquals("https://192.168.1.5:8766/api/m/companion/info", url)
        assertEquals("Bearer secret-token", (headers as Map<*, *>)["Authorization"])
        assertEquals("1", headers[API_HEADER])
        assertNull(body)
    }

    @Test
    fun pairingSendsTheCodeAndANameWithTheRequiredHeaderAndReturnsTheToken() {
        val f = Fake { _, _, _, _ -> Response(200, """{"ok":true,"device":"d1","token":"T0K"}""") }
        val p = CompanionClient("pc:1", f).pair("12345678", "My Pixel")
        assertEquals(Paired("d1", "T0K"), p)
        val (method, url, headers, body) = f.calls.single()
        assertEquals("POST", method)
        assertEquals("https://pc:1/api/m/pair", url)
        assertEquals("1", (headers as Map<*, *>)["X-Jarves"])
        assertTrue((body as String).contains("12345678") && body.contains("My Pixel"))
    }

    @Test
    fun pairingFailuresAreNamedAndABadCodeNeverReachesTheNetwork() {
        fun kind(r: () -> Response): PairError {
            try {
                CompanionClient("pc:1", Fake { _, _, _, _ -> r() }).pair("12345678", "x")
            } catch (e: PairFailed) {
                return e.kind
            }
            fail("should have failed")
            throw IllegalStateException()
        }
        assertEquals(PairError.REFUSED, kind { Response(403, "{}") })
        assertEquals(PairError.RATE, kind { Response(429, "{}") })
        assertEquals(PairError.BAD_ANSWER, kind { Response(200, "nope") })
        assertEquals(PairError.UNREACHABLE, kind { throw IOException("x") })
        val f = Fake { _, _, _, _ -> Response(200, "{}") }
        for (bad in listOf("", "1234567", "123456789", "abcdefgh", "1234 678")) {
            try {
                CompanionClient("pc:1", f).pair(bad, "x")
                fail("accepted $bad")
            } catch (e: PairFailed) {
                assertEquals(PairError.BAD_CODE, e.kind)
            }
        }
        assertTrue(f.calls.isEmpty())
    }

    @Test
    fun theNameIsCappedAndNeverEmpty() {
        val f = Fake { _, _, _, _ -> Response(200, """{"ok":true,"device":"d","token":"t"}""") }
        CompanionClient("pc:1", f).pair("12345678", "x".repeat(100))
        CompanionClient("pc:1", f).pair("12345678", "  ")
        val bodies = f.calls.map { it[3] as String }
        assertFalse(bodies[0].contains("x".repeat(41)))
        assertTrue(bodies[1].contains("Android"))
    }

    @Test
    fun clockSkewIsTheComputersTimeMinusOurs() {
        val i = parseInfo(INFO_ONLINE)!!
        assertEquals(60.5, skewSeconds(i, 1792999940.0), 0.001)
    }

    @Test
    fun aPinMatchesOnlyTheExactCertificate() {
        val cert = "certificate-bytes".toByteArray()
        val pin = Pinning.sha256Hex(cert)
        assertEquals(64, pin.length)
        assertTrue(Pinning.matches(pin, cert))
        assertTrue(Pinning.matches(pin.uppercase(), cert))
        assertFalse(Pinning.matches(pin, "other".toByteArray()))
        assertFalse(Pinning.matches(null, cert))
        assertEquals(pin.take(2) + ":" + pin.substring(2, 4), Pinning.pretty(pin).take(5))
    }
}
