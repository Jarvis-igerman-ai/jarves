package ai.jarves.companion

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

private class PlansRec(val status: Int = 200, val body: String = "{}", val boom: Boolean = false) : Transport {
    var method = ""; var url = ""
    override fun send(method: String, url: String, headers: Map<String, String>, body: String?): Response {
        this.method = method; this.url = url
        if (boom) throw IOException("down")
        return Response(status, this.body)
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class TasksTest {
    private val session = Session("pc:1", "tok", "pin", "dev")
    private val listed = """{"plans":[{"id":1,"title":"a","state":"done","steps":[]}]}"""

    @Test
    fun plansAreReadOnlyGetAndParsedWithStepCounts() {
        val body = """{"enabled":true,"pc_only":["approve","skip","delete"],"plans":[
            {"id":12,"title":"Tidy downloads","state":"running","progress":40,"needs_pc":false,
             "steps":[{"n":1,"status":"done"},{"n":2,"status":"running"},{"n":3,"status":"pending"}]},
            {"id":13,"title":"","state":"draft","progress":0,"needs_pc":true,"steps":[]},
            {"title":"no id"}, 7]}"""
        val rec = PlansRec(body = body)
        val r = CompanionClient("pc:1", rec).plans("t") as Plans.Listed
        assertEquals("GET", rec.method)
        assertTrue(rec.url.endsWith("/api/m/plans"))
        assertTrue(r.enabled)
        assertEquals(listOf(12, 13), r.plans.map { it.id })
        assertEquals(TaskPlan(12, "Tidy downloads", "running", 40, 1, 3, false), r.plans[0])
        assertTrue(r.plans[1].needsPc)
    }

    @Test
    fun progressIsClampedAndTitleCapped() {
        val body = """{"plans":[{"id":1,"title":"${"x".repeat(400)}","state":"done","progress":250,"steps":[]}]}"""
        val p = (CompanionClient("pc:1", PlansRec(body = body)).plans("t") as Plans.Listed).plans.single()
        assertEquals(100, p.progress)
        assertEquals(300, p.title.length)
    }

    @Test
    fun aNullTitleIsEmptyNotTheWordNull() {
        val body = """{"plans":[{"id":1,"title":null,"state":"done","steps":[]}]}"""
        val p = (CompanionClient("pc:1", PlansRec(body = body)).plans("t") as Plans.Listed).plans.single()
        assertEquals("", p.title)
    }

    @Test
    fun plansSwitchedOffOnThePcAreReportedNotHidden() {
        val r = CompanionClient("pc:1", PlansRec(body = """{"enabled":false,"plans":[]}""")).plans("t") as Plans.Listed
        assertEquals(false, r.enabled)
    }

    @Test
    fun planFailuresHavePlainStates() {
        assertEquals(Plans.Revoked, CompanionClient("pc:1", PlansRec(401)).plans("t"))
        assertEquals(Plans.NotAllowed, CompanionClient("pc:1", PlansRec(403)).plans("t"))
        assertEquals(Plans.Unreachable, CompanionClient("pc:1", PlansRec(503)).plans("t"))
        assertEquals(Plans.Unreachable, CompanionClient("pc:1", PlansRec(200, "nope")).plans("t"))
        assertEquals(Plans.Unreachable, CompanionClient("pc:1", PlansRec(boom = true)).plans("t"))
    }

    @Test
    fun stateWordsMapToPlainTextAndUnknownIsNotHidden() {
        assertEquals(R.string.task_state_running, taskStateText("running"))
        assertEquals(R.string.task_state_done, taskStateText("DONE"))
        assertEquals(R.string.task_state_other, taskStateText("weird"))
    }

    @Test
    fun theModelReadsOnceAndANewerReadReplacesTheOlder() = runTest {
        val pc = PlansRec(body = listed)
        var reads = 0
        val m = TasksModel(this, StandardTestDispatcher(testScheduler)) { reads++; CompanionClient(it.base, pc) }
        m.refresh(session); m.refresh(session)               // the first read is cancelled before it runs
        advanceUntilIdle()
        assertEquals(1, reads)
        assertEquals(1, (m.state.value as Plans.Listed).plans.size)
    }

    @Test
    fun aNewPairingStartsEmpty() = runTest {
        val m = TasksModel(this, StandardTestDispatcher(testScheduler)) { CompanionClient(it.base, PlansRec(body = listed)) }
        m.refresh(session); advanceUntilIdle()
        assertTrue(m.state.value is Plans.Listed)
        m.refresh(session.copy(token = "other"))
        assertNull(m.state.value)                            // nothing from the old pairing is shown
        advanceUntilIdle()
        assertTrue(m.state.value is Plans.Listed)
    }

    @Test
    fun aCrashingReadIsUnreachableButCancellationIsNotSwallowed() = runTest {
        val bad = TasksModel(this, StandardTestDispatcher(testScheduler)) { throw IllegalStateException("x") }
        bad.refresh(session); advanceUntilIdle()
        assertEquals(Plans.Unreachable, bad.state.value)
        val cancelled = TasksModel(this, StandardTestDispatcher(testScheduler)) { throw CancellationException("gone") }
        cancelled.refresh(session); advanceUntilIdle()
        assertNull(cancelled.state.value)                    // rethrown, not turned into a failure line
    }

    @Test
    fun theCardNeedsTheScopeAndHidesOnRefusalOrUnpair() {
        assertFalse(tasksCardVisible(listOf("chat", "status"), null))
        assertFalse(tasksCardVisible(listOf("chat"), Plans.Listed(emptyList(), true)))
        assertTrue(tasksCardVisible(listOf("tasks"), null))
        assertTrue(tasksCardVisible(listOf("tasks"), Plans.Unreachable))
        assertTrue(tasksCardVisible(listOf("tasks"), Plans.Listed(emptyList(), false)))
        assertFalse(tasksCardVisible(listOf("tasks"), Plans.NotAllowed))
        assertFalse(tasksCardVisible(listOf("tasks"), Plans.Revoked))
    }

    @Test
    fun eachStatusTickIsANewLinkSoTheCardReadsAgain() {
        fun info(t: Double) = """{"api":1,"api_min":1,"core_version":"4","server_time":$t,"pc":{"state":"online","core":"running"},"scopes":["tasks"]}"""
        val a = CompanionClient("pc:1", PlansRec(body = info(1.0))).check("t")
        val b = CompanionClient("pc:1", PlansRec(body = info(11.0))).check("t")
        assertTrue(a is Link.Online)
        assertNotEquals(a, b)                                // the PC sends time.time() per answer (mobile_companion.py)
    }
}
