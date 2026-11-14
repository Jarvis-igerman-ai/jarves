package ai.jarves.companion

import org.junit.Assert.assertEquals
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

class TasksTest {
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
}
