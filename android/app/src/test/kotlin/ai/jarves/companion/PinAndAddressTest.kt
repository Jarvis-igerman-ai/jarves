package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate

private const val PEM = """-----BEGIN CERTIFICATE-----
MIIBFzCBv6ADAgECAgEBMAoGCCqGSM49BAMCMBYxFDASBgNVBAMMC2phcnZlcy10
ZXN0MB4XDTI2MDEwMTAwMDAwMFoXDTM1MTIzMDAwMDAwMFowFjEUMBIGA1UEAwwL
amFydmVzLXRlc3QwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAARTW4gztB4zOLd4
cu9U92FYTj3/gAWrolKIEHzOQox2TeUE1e19aaqjGf8Uj2zUzfri8Qi9G0pDxWgF
HUWLrrLMMAoGCCqGSM49BAMCA0cAMEQCIHntUcmvmO2sN2/4wgyYSnVP4g+MJkBG
1emej/kXF90qAiA5w+ayCKELUaOKWWNdJCzJvlafDe4wE2kz54vaSvOf1A==
-----END CERTIFICATE-----"""
private const val PEM_SHA256 = "89cb3cf3d5ef0689f4981a650507dfbaa5932ab039c5a9cedce2635707223b9b"

private fun cert(): X509Certificate =
    CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(PEM.toByteArray())) as X509Certificate

class PinAndAddressTest {
    @Test
    fun theFingerprintOfARealCertificateIsItsDerSha256() {
        assertEquals(PEM_SHA256, Pinning.sha256Hex(cert().encoded))
    }

    @Test
    fun aMatchingPinIsTrustedAndTheLeafFingerprintIsRecorded() {
        val t = PinTrust(PEM_SHA256)
        t.checkServerTrusted(arrayOf(cert()), "ECDSA")
        assertEquals(PEM_SHA256, t.seen)
    }

    @Test
    fun aDifferentCertificateIsRefusedEvenThoughItIsRecorded() {
        val t = PinTrust("00".repeat(32))
        try {
            t.checkServerTrusted(arrayOf(cert()), "ECDSA")
            fail("a changed certificate was trusted")
        } catch (e: CertificateException) {
            assertEquals(PEM_SHA256, t.seen)
        }
    }

    @Test
    fun captureModeRecordsTheFingerprintAndNothingElse() {
        val t = PinTrust(null)
        t.checkServerTrusted(arrayOf(cert()), "ECDSA")
        assertEquals(PEM_SHA256, t.seen)
        try {
            t.checkServerTrusted(emptyArray(), "ECDSA")
            fail("an empty chain was trusted")
        } catch (e: CertificateException) {
            // expected
        }
        try {
            t.checkClientTrusted(arrayOf(cert()), "ECDSA")
            fail("client auth accepted")
        } catch (e: CertificateException) {
            // expected
        }
    }

    @Test
    fun onlyHostAndPortAreAccepted() {
        for (ok in listOf("192.168.1.20:8766", "pc.local:8766", "my-pc:1", "[fe80::1]:8766", "100.64.1.2:65535")) assertEquals(ok, parseBase(ok))
        assertEquals("192.168.1.20:8766", parseBase("  192.168.1.20:8766 "))
        for (bad in listOf("", "pc", "pc:0", "pc:65536", "pc:99999", "pc:", ":8766", "http://pc:8766", "https://pc:8766", "pc:8766/x", "pc:8766?x=1",
            "pc:8766#f", "user@pc:8766", "user:pw@pc:8766", "pc:8766\r\nX: y", "pc :8766", "pc\\evil:8766", "-pc:8766", "pc-:8766", "[::1:8766", "p c:8766")) {
            assertNull("accepted <$bad>", parseBase(bad))
        }
    }

    @Test
    fun aChangedCertificateIsNotOfflineAndOtherOddnessNeverCrashes() {
        fun link(t: Transport) = CompanionClient("pc:1", t).check("tok")
        assertEquals(Link.CertChanged, link(object : Transport {
            override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response = throw CertChangedException("c")
        }))
        assertEquals(Link.Unreachable, link(object : Transport {
            override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response = throw IllegalArgumentException("port out of range")
        }))
        assertEquals(Link.Unreachable, link(object : Transport {
            override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response = throw IOException("down")
        }))
        try {
            CompanionClient("pc:1", object : Transport {
                override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response = throw IllegalStateException("x")
            }).pair("12345678", "n")
            fail("pairing did not fail")
        } catch (e: PairFailed) {
            assertEquals(PairError.UNREACHABLE, e.kind)
        }
        assertTrue(CertChangedException("x") is IOException)
        assertFalse(Link.CertChanged == Link.Unreachable)
    }
}
