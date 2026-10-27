package ai.jarves.companion

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/** A fake PC: answers GET approvals with [list], POST decisions with [decide] (or throws when [down]); records every POST body. */
private class FakePc(var list: String, var decide: Int = 200, var down: Boolean = false) : Transport {
    val posts = mutableListOf<String>()
    var gets = 0
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
        if (method == "POST") posts += body ?: "" else gets++
        if (down && method == "POST") throw IOException("down")
        return if (method == "GET") Response(200, list) else Response(decide, "{}")
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class ApprovalsUiShareUiTest {
    private val id1 = "0123456789abcdef"
    private val id2 = "fedcba9876543210"
    private val a1 = Approval(id1, "Run tests", "command", "low", 50, phoneCan = true)
    private val a2 = Approval(id2, "Delete files", "x", "high", 9, phoneCan = false)
    private val session = Session("pc:1", "tok", "ab", "dev")
    private val listBody = """{"phone_approvals":true,"pending":[
        {"id":"$id1","title":"Run tests","kind":"command","risk":"low","seconds_left":50},
        {"id":"$id2","title":"Delete files","kind":"x","risk":"high","seconds_left":9,"typed":true}]}"""

    @Test
    fun approveIsOfferedOnlyWhenThePhoneMay() {
        assertEquals(ApprovalButtons(true, DenyReason.NONE), approvalButtons(a1, true))
        assertEquals(ApprovalButtons(false, DenyReason.PC_ONLY), approvalButtons(a2, true))
        assertEquals(ApprovalButtons(false, DenyReason.PHONE_OFF), approvalButtons(a1, false))
        assertEquals(ApprovalButtons(false, DenyReason.PHONE_OFF), approvalButtons(a2, false))
        assertNull(reasonText(DenyReason.NONE))
        assertNotNull(reasonText(DenyReason.PHONE_OFF)); assertNotNull(reasonText(DenyReason.PC_ONLY))
    }

    @Test
    fun riskIsTextAndUnknownIsHigh() {
        assertEquals(Risk.LOW, riskOf("low")); assertEquals(Risk.MEDIUM, riskOf("Medium")); assertEquals(Risk.MEDIUM, riskOf("med"))
        assertEquals(Risk.HIGH, riskOf("high")); assertEquals(Risk.HIGH, riskOf("")); assertEquals(Risk.HIGH, riskOf("weird"))
        assertEquals(3, Risk.entries.map(::riskText).toSet().size)
    }

    @Test
    fun everyDecisionHasItsOwnPlainMessage() {
        val msgs = Decision.entries.filter { it != Decision.DONE }.map { decisionText(it, true) } +
            listOf(decisionText(Decision.DONE, true), decisionText(Decision.DONE, false))
        assertEquals(msgs.size, msgs.toSet().size)
        assertEquals(decisionText(Decision.GONE, true), decisionText(Decision.GONE, false))
        assertEquals(setOf(Decision.UNREACHABLE, Decision.RATE), Decision.entries.filter(::canRetry).toSet())
    }

    @Test
    fun aTapIsRefusedLocallyWhenItMustNotBeSent() {
        val listed = ApprovalsState(token = "tok", list = Approvals.Listed(listOf(a1, a2), true))
        assertEquals(Tap(id1, true, "c1"), startTap(listed, id1, true) { "c1" })
        assertNull(startTap(listed, id2, true) { "c1" })                       // PC-only: no approve, even if asked
        assertEquals(Tap(id2, false, "c1"), startTap(listed, id2, false) { "c1" })
        assertNull(startTap(listed, "aaaaaaaaaaaaaaaa", false) { "c1" })         // not in the list
        assertNull(startTap(listed.copy(sending = Tap(id1, true, "c0")), id2, false) { "c1" })   // one at a time
        val off = ApprovalsState(list = Approvals.Listed(listOf(a1), false))
        assertNull(startTap(off, id1, true) { "c1" })
        assertNull(startTap(ApprovalsState(list = Approvals.Unreachable), id1, false) { "c1" })
    }

    @Test
    fun retryKeepsTheSameCommandIdAndOnlyForUncertainAnswers() {
        val tap = Tap(id1, true, "c1")
        val s = afterDecision(ApprovalsState(sending = tap), tap, Decision.UNREACHABLE)
        assertNull(s.sending)
        assertEquals(tap, retryTap(s))
        assertNull(retryTap(afterDecision(s, tap, Decision.DONE)))
        assertNull(retryTap(afterDecision(s, tap, Decision.GONE)))
        assertEquals(tap, retryTap(afterDecision(s, tap, Decision.RATE)))
    }

    @Test
    fun theModelSendsOneDecisionPerTapAndRefreshesAfter() = runTest {
        val pc = FakePc(listBody)
        val m = ApprovalsModel(this, StandardTestDispatcher(testScheduler)) { CompanionClient(it.base, pc) }
        m.refresh(session); m.refresh(session)                // a second refresh while one runs is skipped
        advanceUntilIdle()
        assertEquals(1, pc.gets)
        assertEquals(2, (m.state.value.list as Approvals.Listed).pending.size)
        m.tap(session, id1, true); m.tap(session, id1, true)  // a double tap sends once
        advanceUntilIdle()
        assertEquals(1, pc.posts.size)
        assertTrue(pc.posts[0].contains("\"approved\":true"))
        assertEquals(Decision.DONE, m.state.value.last!!.decision)
        assertEquals(2, pc.gets)                              // refreshed after the decision
        m.tap(session, id2, true)                             // approve on a PC-only one: nothing is sent
        advanceUntilIdle()
        assertEquals(1, pc.posts.size)
    }

    @Test
    fun aRetryResendsTheSameCommandId() = runTest {
        val pc = FakePc(listBody, down = true)
        val m = ApprovalsModel(this, StandardTestDispatcher(testScheduler)) { CompanionClient(it.base, pc) }
        m.refresh(session); advanceUntilIdle()
        m.tap(session, id2, false); advanceUntilIdle()
        assertEquals(Decision.UNREACHABLE, m.state.value.last!!.decision)
        pc.down = false
        m.retry(session); advanceUntilIdle()
        assertEquals(2, pc.posts.size)
        assertEquals(pc.posts[0], pc.posts[1])               // same body = same command id
        assertEquals(Decision.DONE, m.state.value.last!!.decision)
        m.retry(session); advanceUntilIdle()                  // nothing to retry after DONE
        assertEquals(2, pc.posts.size)
    }

    @Test
    fun aNewSessionStartsClean() = runTest {
        val pc = FakePc(listBody)
        val m = ApprovalsModel(this, StandardTestDispatcher(testScheduler)) { CompanionClient(it.base, pc) }
        m.refresh(session); advanceUntilIdle()
        m.refresh(session.copy(token = "other"))
        assertEquals("other", m.state.value.token)
        assertNull(m.state.value.list)
        advanceUntilIdle()
    }

    @Test
    fun onlyPlainTextSharesAreAccepted() {
        assertEquals("buy milk", sharedTextOf(ACTION_SEND, "text/plain", "  buy milk "))
        assertEquals("hi", sharedTextOf(ACTION_SEND, "text/plain; charset=utf-8", "hi"))
        assertEquals("hi", sharedTextOf(ACTION_SEND, "TEXT/PLAIN", "hi"))
        assertNull(sharedTextOf(ACTION_SEND, "image/png", "hi"))
        assertNull(sharedTextOf(ACTION_SEND, "text/html", "<b>hi</b>"))
        assertNull(sharedTextOf(ACTION_SEND, null, "hi"))
        assertNull(sharedTextOf("android.intent.action.SEND_MULTIPLE", "text/plain", "hi"))
        assertNull(sharedTextOf("android.intent.action.MAIN", "text/plain", "hi"))
        assertNull(sharedTextOf(ACTION_SEND, "text/plain", null))
        assertNull(sharedTextOf(ACTION_SEND, "text/plain", "   \u0000 "))
        assertEquals(SHARE_MAX_CHARS, sharedTextOf(ACTION_SEND, "text/plain", "x".repeat(SHARE_MAX_CHARS + 9))!!.length)
    }

    @Test
    fun aShareIsDroppedWhenNotPairedAndNeverStartsSending() {
        assertEquals(ShareState.None, offerShare(ShareState.Editing("old"), "new", paired = false))
        assertEquals(ShareState.Editing("new"), offerShare(ShareState.None, "new", paired = true))
        assertEquals(ShareState.Editing("old"), offerShare(ShareState.Editing("old"), null, paired = true))
        val sending = ShareState.Sending("a", "c1")
        assertEquals(sending, offerShare(sending, "new", paired = true))
        // offering never produces a Sending state: only Send does
        assertTrue(offerShare(ShareState.None, "x", true) is ShareState.Editing)
    }

    @Test
    fun sendMakesOneIdAndRetryKeepsIt() {
        assertEquals(ShareState.Sending("hi", "c1"), startShare(ShareState.Editing("hi")) { "c1" })
        assertNull(startShare(ShareState.Editing("   ")) { "c1" })
        assertNull(startShare(ShareState.None) { "c1" })
        assertNull(startShare(ShareState.Sending("hi", "c0")) { "c1" })
        assertEquals(ShareState.Sending("hi", "c0"), retryShare(ShareState.Result("hi", "c0", Shared.Unreachable)))
        assertNull(retryShare(ShareState.Result("hi", "c0", Shared.Answer("ok"))))
        assertNull(retryShare(ShareState.Result("hi", "c0", Shared.Revoked)))
        assertNull(shareFailureText(Shared.Answer("x")))
        val fails = listOf(Shared.Busy, Shared.RateLimited, Shared.NotAllowed, Shared.Revoked, Shared.Unreachable).map(::shareFailureText)
        assertEquals(fails.size, fails.filterNotNull().toSet().size)
    }

    @Test
    fun theShareModelSendsOnlyOnSend() = runTest {
        val sent = mutableListOf<String>()
        val t = object : Transport {
            override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
                sent += body ?: ""
                return Response(200, """{"reply":"noted"}""")
            }
        }
        val m = ShareModel(this, StandardTestDispatcher(testScheduler)) { CompanionClient(it.base, t) }
        m.edit("ignored")                                     // no share pending: editing does nothing
        assertEquals(ShareState.None, m.state.value)
        advanceUntilIdle()
        assertEquals(0, sent.size)
        m.retry(session); advanceUntilIdle()
        assertEquals(0, sent.size)
        m.offer("buy milk", paired = true); advanceUntilIdle()
        assertEquals(ShareState.Editing("buy milk"), m.state.value)
        assertEquals(0, sent.size)                            // shown, not sent
        m.edit("buy oat milk")
        m.send(session); m.send(session)                      // a double tap sends once
        advanceUntilIdle()
        assertEquals(1, sent.size)
        assertTrue(sent[0].contains("buy oat milk"))
        val r = m.state.value as ShareState.Result
        assertEquals(Shared.Answer("noted"), r.result)
        m.clear()
        assertEquals(ShareState.None, m.state.value)
        m.offer("later", paired = false)
        assertEquals(ShareState.None, m.state.value)
    }
}
