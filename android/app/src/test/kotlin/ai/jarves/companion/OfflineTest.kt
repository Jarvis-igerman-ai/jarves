package ai.jarves.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OfflineTest {
    private class MemStorage(var text: String? = null) : Storage {
        override fun read() = text
        override fun write(text: String) { this.text = text }
    }

    private class FakeAlarms : AlarmScheduler {
        val set = LinkedHashMap<String, Long>()
        override fun schedule(id: String, atMs: Long) { set[id] = atMs }
        override fun cancel(id: String) { set.remove(id) }
    }

    private var now = 1_000L
    private var n = 0
    private fun store(st: Storage = MemStorage(), al: AlarmScheduler = FakeAlarms(), dev: String = "A") =
        OfflineStore(st, al, dev, { now }, { "id${n++}" })

    @Test fun notesPersistAcrossRestart() {
        val st = MemStorage()
        store(st).addNote("  buy milk ")
        assertEquals(listOf("buy milk"), store(st).list(Kind.NOTE).map { it.text })
    }

    @Test fun reminderSchedulesAndCompleteCancels() {
        val al = FakeAlarms()
        val s = store(al = al)
        val r = s.addReminder("call", 5_000)
        assertEquals(5_000L, al.set[r.id])
        s.complete(r.id)
        assertTrue(al.set.isEmpty())
        assertTrue(s.list().single().done)
    }

    @Test fun timerDueIsNowPlusDuration() {
        val al = FakeAlarms()
        val t = store(al = al).startTimer("tea", 90_000)
        assertEquals(91_000L, t.dueAtMs)
        assertEquals(91_000L, al.set[t.id])
    }

    @Test(expected = IllegalArgumentException::class) fun timerRejectsZero() {
        store().startTimer("x", 0)
    }

    @Test fun deleteLeavesTombstoneHiddenFromList() {
        val s = store()
        val a = s.addNote("a")
        s.delete(a.id)
        assertTrue(s.list().isEmpty())
        assertTrue(s.all().single().deleted)
        assertNull(s.editText(a.id, "b"))
    }

    @Test fun rescheduleAllAfterBootReturnsOverdue() {
        val al = FakeAlarms()
        val s = store(al = al)
        val past = s.addReminder("past", 2_000)
        val future = s.addReminder("future", 9_000)
        val doneOne = s.addReminder("done", 8_000)
        s.complete(doneOne.id)
        al.set.clear()
        now = 3_000
        assertEquals(listOf(past.id), s.rescheduleAll().map { it.id })
        assertEquals(mapOf(future.id to 9_000L), al.set)
    }

    @Test fun missedWhilePhoneOffAreReturnedUntilShown() {
        val st = MemStorage()
        val s = store(st)
        val a = s.addReminder("a", 2_000)
        val b = s.startTimer("b", 1_500)
        now = 10_000 // phone was off past both due times
        val restarted = store(st)
        assertEquals(listOf(a.id, b.id), restarted.rescheduleAll().map { it.id })
        restarted.complete(a.id) // the caller shows it and marks it done
        assertEquals(listOf(b.id), restarted.rescheduleAll().map { it.id })
    }

    @Test fun damagedFileStartsEmpty() {
        assertTrue(store(MemStorage("{not json")).all().isEmpty())
    }

    @Test fun alarmModeFallsBackWithoutPermission() {
        assertEquals(AlarmMode.Mode.EXACT, AlarmMode.choose(true))
        assertEquals(AlarmMode.Mode.INEXACT, AlarmMode.choose(false))
    }

    private fun item(id: String, t: Long, dev: String = "A", del: Boolean = false, text: String = "x") =
        Item(id, Kind.NOTE, text, null, t, dev, deleted = del)

    @Test fun mergeLaterEditWins() {
        val m = SyncMerge.merge(listOf(item("1", 5, text = "old")), listOf(item("1", 9, "B", text = "new")))
        assertEquals("new", m.single().text)
    }

    @Test fun mergeTieDeleteWinsAndIsCommutative() {
        val a = item("1", 5, "A")
        val b = item("1", 5, "B", del = true)
        assertEquals(SyncMerge.merge(listOf(a), listOf(b)), SyncMerge.merge(listOf(b), listOf(a)))
        assertTrue(SyncMerge.merge(listOf(a), listOf(b)).single().deleted)
    }

    @Test fun mergeIsIdempotentAndUnionsDisjoint() {
        val l = listOf(item("1", 1), item("2", 2))
        val r = listOf(item("3", 3))
        val m = SyncMerge.merge(l, r)
        assertEquals(setOf("1", "2", "3"), m.map { it.id }.toSet())
        assertEquals(m.toSet(), SyncMerge.merge(m, r).toSet())
        assertEquals(listOf("1", "2"), SyncMerge.toPush(m, r).map { it.id })
    }

    @Test fun applyMergedCancelsRemoteDeleteAndSchedulesRemoteReminder() {
        val al = FakeAlarms()
        val s = store(al = al)
        val mine = s.addReminder("mine", 9_000)
        val theirs = Item("r1", Kind.REMINDER, "theirs", 8_000, 2_000, "B")
        s.applyMerged(listOf(mine.copy(deleted = true, updatedAtMs = 3_000), theirs))
        assertEquals(mapOf("r1" to 8_000L), al.set)
        assertEquals(listOf("theirs"), s.list().map { it.text })
    }

    @Test fun itemJsonRoundTrip() {
        val i = Item("1", Kind.TIMER, "t", 7, 3, "A", done = true)
        assertEquals(i, Item.fromJson(i.toJson()))
    }
}
