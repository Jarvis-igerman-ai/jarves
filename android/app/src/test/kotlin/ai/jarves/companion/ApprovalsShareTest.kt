package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

private class Rec(val status: Int = 200, val body: String = "{}", val boom: Boolean = false) : Transport {
    var method = ""; var url = ""; var sentBody: String? = null; var calls = 0
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
        calls++; this.method = method; this.url = url; sentBody = body
        if (boom) throw IOException("down")
        return Response(status, this.body)
    }
}

class ApprovalsShareTest {
    private val id1 = "0123456789abcdef"

    @Test
    fun theListMarksWhatThePhoneMayApproveAndDropsMalformedIds() {
        val body = """{"phone_approvals":true,"pending":[
            {"id":"$id1","title":"Run tests","kind":"command","risk":"low","seconds_left":50,"typed":false,"pc_only":false},
            {"id":"fedcba9876543210","title":"Delete","kind":"x","risk":"high","seconds_left":9,"typed":true,"pc_only":false},
            {"id":"aaaaaaaaaaaaaaaa","title":"Git","kind":"github","risk":"med","seconds_left":9,"typed":false,"pc_only":true},
            {"id":"../etc","title":"bad"}, 5]}"""
        val r = CompanionClient("pc:1", Rec(body = body)).approvals("t") as Approvals.Listed
        assertEquals(3, r.pending.size)
        assertEquals(listOf(true, false, false), r.pending.map { it.phoneCan })
        assertTrue(r.phoneApprovalsOn)
    }

    @Test
    fun whenPhoneApprovalsAreOffNothingIsApprovable() {
        val body = """{"phone_approvals":false,"pending":[{"id":"$id1","title":"t","kind":"k","risk":"low","seconds_left":5}]}"""
        val r = CompanionClient("pc:1", Rec(body = body)).approvals("t") as Approvals.Listed
        assertFalse(r.pending[0].phoneCan); assertFalse(r.phoneApprovalsOn)
    }

    @Test
    fun listFailuresHavePlainStates() {
        assertEquals(Approvals.Revoked, CompanionClient("pc:1", Rec(401)).approvals("t"))
        assertEquals(Approvals.NotAllowed, CompanionClient("pc:1", Rec(403)).approvals("t"))
        assertEquals(Approvals.Unreachable, CompanionClient("pc:1", Rec(500)).approvals("t"))
        assertEquals(Approvals.Unreachable, CompanionClient("pc:1", Rec(200, "nope")).approvals("t"))
        assertEquals(Approvals.Unreachable, CompanionClient("pc:1", Rec(boom = true)).approvals("t"))
    }

    @Test
    fun aDecisionGoesToTheRightRouteWithOneCommandIdAndEveryRefusalIsNamed() {
        val t = Rec()
        assertEquals(Decision.DONE, CompanionClient("pc:1", t).decide("tok", id1, true, "cmd-12345678"))
        assertEquals("POST", t.method); assertEquals("https://pc:1/api/m/approvals/$id1", t.url)
        assertTrue(t.sentBody!!.contains("\"approved\":true") && t.sentBody!!.contains("cmd-12345678"))
        fun d(status: Int, body: String = "{}") = CompanionClient("pc:1", Rec(status, body)).decide("t", id1, true)
        assertEquals(Decision.OFF, d(403, """{"code":"PHONE_APPROVALS_OFF"}"""))
        assertEquals(Decision.PC_ONLY, d(403, """{"code":"TYPED_PC_ONLY"}""")); assertEquals(Decision.PC_ONLY, d(403, """{"code":"PC_ONLY"}"""))
        assertEquals(Decision.NOT_ALLOWED, d(403)); assertEquals(Decision.GONE, d(409)); assertEquals(Decision.RATE, d(429))
        assertEquals(Decision.REVOKED, d(401)); assertEquals(Decision.UNREACHABLE, d(503))
        assertEquals(Decision.UNREACHABLE, CompanionClient("pc:1", Rec(boom = true)).decide("t", id1, false))
    }

    @Test
    fun aBadApprovalIdNeverReachesTheNetwork() {
        val t = Rec()
        assertEquals(Decision.GONE, CompanionClient("pc:1", t).decide("tok", "../../x", true))
        assertEquals(0, t.calls)
    }

    @Test
    fun commandIdsAreFreshAndAcceptedByTheServerRule() {
        val a = newCommandId(); val b = newCommandId()
        assertTrue(a != b && Regex("[A-Za-z0-9_.:-]{8,64}").matches(a))
    }

    @Test
    fun sharedTextIsCleanedAndCutAndOnlyTextIsSent() {
        assertEquals("", cleanShared(null)); assertEquals("hi", cleanShared("  hi\u0000 "))
        assertEquals(SHARE_MAX_CHARS, cleanShared("x".repeat(SHARE_MAX_CHARS + 50)).length)
        val t = Rec(body = """{"reply":"done","ok":true}""")
        assertEquals(Shared.Answer("done"), CompanionClient("pc:1", t).share("tok", " buy milk ", "cmd-12345678"))
        assertEquals("https://pc:1/api/m/chat", t.url)
        assertTrue(t.sentBody!!.contains("\"text\":\"buy milk\""))
        val none = Rec()
        assertEquals(Shared.Answer(""), CompanionClient("pc:1", none).share("tok", "   "))
        assertEquals(0, none.calls)
    }

    @Test
    fun shareFailuresHavePlainStates() {
        fun s(status: Int) = CompanionClient("pc:1", Rec(status)).share("t", "hello")
        assertEquals(Shared.Revoked, s(401)); assertEquals(Shared.NotAllowed, s(403)); assertEquals(Shared.Busy, s(409))
        assertEquals(Shared.RateLimited, s(429)); assertEquals(Shared.Unreachable, s(502))
        assertEquals(Shared.Unreachable, CompanionClient("pc:1", Rec(boom = true)).share("t", "hello"))
    }
}
