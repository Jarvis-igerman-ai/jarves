package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

private class VoiceFake(val status: Int = 200, val body: String = "", val boom: Boolean = false) : Transport {
    var calls = 0
    var sent: ByteArray? = null
    var url = ""
    var headers: Map<String, String> = emptyMap()
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response = throw IOException("text not used")
    override fun sendBytes(method: String, url: String, headers: Map<String, String>, body: ByteArray): Response {
        calls++; sent = body; this.url = url; this.headers = headers
        if (boom) throw IOException("down")
        return Response(status, this.body)
    }
}

class VoiceTest {
    private val speech = ShortArray(VOICE_RATE) { (it % 100).toShort() }

    @Test
    fun theWavHeaderIsCanonical() {
        val w = Wav.encode(ShortArray(3) { (it + 1).toShort() })
        assertEquals(44 + 6, w.size)
        assertEquals("RIFF", String(w, 0, 4)); assertEquals("WAVE", String(w, 8, 4)); assertEquals("fmt ", String(w, 12, 4)); assertEquals("data", String(w, 36, 4))
        val b = java.nio.ByteBuffer.wrap(w).order(java.nio.ByteOrder.LITTLE_ENDIAN)
        assertEquals(36 + 6, b.getInt(4)); assertEquals(1, b.getShort(20).toInt()); assertEquals(1, b.getShort(22).toInt())
        assertEquals(16000, b.getInt(24)); assertEquals(32000, b.getInt(28)); assertEquals(16, b.getShort(34).toInt()); assertEquals(6, b.getInt(40))
        assertEquals(2, b.getShort(46).toInt())
    }

    @Test
    fun aTapIsNeverSent() {
        val t = VoiceFake()
        assertEquals(Voice.NoSpeech, CompanionClient("pc:1", t).voice("tok", ShortArray(10)))
        assertEquals(0, t.calls)
    }

    @Test
    fun aGoodAnswerIsShownAndTheRequestGoesToTheVoiceRouteWithTheToken() {
        val t = VoiceFake(body = """{"transcript":"hello","reply":"hi there","ok":true}""")
        assertEquals(Voice.Heard("hello", "hi there"), CompanionClient("pc:1", t).voice("tok", speech))
        assertEquals("https://pc:1/api/m/voice", t.url)
        assertEquals("Bearer tok", t.headers["Authorization"]); assertEquals("audio/wav", t.headers["Content-Type"])
        assertEquals("RIFF", String(t.sent!!, 0, 4))
    }

    @Test
    fun aRecordingIsCutAtTheServerLimit() {
        val t = VoiceFake(body = """{"transcript":"x","reply":"y","ok":true}""")
        CompanionClient("pc:1", t).voice("tok", ShortArray(VOICE_RATE * 120))
        assertTrue(t.sent!!.size <= 2_000_000)
        assertEquals(44 + VOICE_RATE * VOICE_MAX_SECONDS * 2, t.sent!!.size)
    }

    @Test
    fun everyRefusalHasItsOwnPlainReason() {
        fun v(status: Int, body: String = "{}") = CompanionClient("pc:1", VoiceFake(status, body)).voice("t", speech)
        assertEquals(Voice.NoSpeech, v(200, """{"transcript":"","reply":"","ok":false,"code":"NO_SPEECH"}"""))
        assertEquals(Voice.Revoked, v(401)); assertEquals(Voice.NotAllowed, v(403)); assertEquals(Voice.RateLimited, v(429))
        assertEquals(Voice.Busy, v(409, """{"code":"BUSY"}""")); assertEquals(Voice.NeedsSecureLink, v(409, """{"error":"voice needs TLS"}"""))
        assertEquals(Voice.Unreachable, v(502)); assertEquals(Voice.Unreachable, v(200, "not json"))
        assertEquals(Voice.Unreachable, CompanionClient("pc:1", VoiceFake(boom = true)).voice("t", speech))
    }
}
