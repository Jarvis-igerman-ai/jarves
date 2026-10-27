package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.InputStream

class ReadCappedTest {
    @Test
    fun stopsAtTheCapAndNeverReadsTheRest() {
        var served = 0
        val endless = object : InputStream() {
            override fun read(): Int { served++; return 'a'.code }
            override fun read(b: ByteArray, off: Int, len: Int): Int { java.util.Arrays.fill(b, off, off + len, 'a'.code.toByte()); served += len; return len }
        }
        assertEquals(1000, readCapped(endless, 1000).length)
        assertEquals(1000, served)
    }

    @Test
    fun shortAnswersComeBackWhole() {
        assertEquals("hello", readCapped(ByteArrayInputStream("hello".toByteArray()), 1000))
        assertEquals("", readCapped(ByteArrayInputStream(ByteArray(0)), 1000))
    }
}
