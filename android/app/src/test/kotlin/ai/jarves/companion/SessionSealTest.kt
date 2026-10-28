package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test
import javax.crypto.KeyGenerator

class SessionSealTest {
    private fun sealer() = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey().let { k -> AesGcmSealer { k } }
    private val s = Session("192.168.1.20:8766", "secret-token-123", "ab".repeat(32), "Pixel")

    @Test
    fun aSessionRoundTripsAndTheTokenIsNotInTheStoredText() {
        val z = sealer()
        val text = SessionCodec.encode(s, z)
        assertFalse(text.contains("secret-token-123"))
        assertFalse(String(java.util.Base64.getDecoder().decode(text), Charsets.ISO_8859_1).contains("secret-token"))
        assertEquals(s, SessionCodec.decode(text, z))
    }

    @Test
    fun everySealUsesAFreshNonce() {
        val z = sealer()
        assertNotEquals(SessionCodec.encode(s, z), SessionCodec.encode(s, z))
    }

    @Test
    fun anotherKeyATamperedBlobAndGarbageAreRefusedNotCrashed() {
        val text = SessionCodec.encode(s, sealer())
        assertNull(SessionCodec.decode(text, sealer()))                     // a different key (the Keystore key was lost)
        val z = sealer()
        val good = java.util.Base64.getDecoder().decode(SessionCodec.encode(s, z))
        good[good.size - 1] = (good[good.size - 1].toInt() xor 1).toByte()  // one flipped bit
        assertNull(SessionCodec.decode(java.util.Base64.getEncoder().encodeToString(good), z))
        for (bad in listOf("", "not base64 !!", "AAAA", java.util.Base64.getEncoder().encodeToString(ByteArray(40)))) assertNull(SessionCodec.decode(bad, z))
    }
}
