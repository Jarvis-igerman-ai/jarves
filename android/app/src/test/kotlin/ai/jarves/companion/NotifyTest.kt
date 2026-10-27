package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

private class SyncFake(val status: Int = 200, val body: String = "{}", val boom: Boolean = false) : Transport {
    var url = ""
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
        this.url = url
        if (boom) throw IOException("down")
        return Response(status, this.body)
    }
}

class NotifyTest {
    @Test
    fun onlyAGrowingCountNotifiesAndEmptyResetsTheMemory() {
        assertFalse(NotifyPolicy.shouldNotify(0, 0))
        assertTrue(NotifyPolicy.shouldNotify(1, 0))
        assertFalse(NotifyPolicy.shouldNotify(1, 1))      // the same one is not announced twice
        assertFalse(NotifyPolicy.shouldNotify(2, 3))
        assertTrue(NotifyPolicy.shouldNotify(3, 2))
        assertEquals(0, NotifyPolicy.nextTold(0, 5)); assertEquals(2, NotifyPolicy.nextTold(2, 5)); assertEquals(0, NotifyPolicy.nextTold(-4, 1))
        assertTrue(NotifyPolicy.shouldNotify(1, NotifyPolicy.nextTold(0, 3)))   // empty, then a new one: told again
    }

    @Test
    fun waitingReadsOnlyTheCountFromTheStatusPoll() {
        val t = SyncFake(body = """{"pending":[{"id":"a","title":"secret"},{"id":"b"},{"id":"c"}],"phone_approvals":false}""")
        assertEquals(3, CompanionClient("pc:1", t).waiting("tok"))
        assertTrue(t.url.endsWith("/api/m/approvals"))
        assertEquals(0, CompanionClient("pc:1", SyncFake(body = "{}")).waiting("tok"))
        assertEquals(999, CompanionClient("pc:1", SyncFake(body = """{"pending":[""" + "{},".repeat(1200) + """{}]}""")).waiting("tok"))
    }

    @Test
    fun whenThePcDoesNotAnswerSensiblyThereIsNoCountAndSoNoNotification() {
        assertNull(CompanionClient("pc:1", SyncFake(401)).waiting("t"))
        assertNull(CompanionClient("pc:1", SyncFake(500)).waiting("t"))
        assertNull(CompanionClient("pc:1", SyncFake(body = "nope")).waiting("t"))
        assertNull(CompanionClient("pc:1", SyncFake(boom = true)).waiting("t"))
    }
}
