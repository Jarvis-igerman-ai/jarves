package ai.jarves.companion

import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

/**
 * Offline basics (T04, #394): notes, reminders and timers kept on the phone, working with no network. Pure Kotlin so the JVM tests cover it;
 * Android specifics (file, AlarmManager) sit behind [Storage] and [AlarmScheduler]. Sync is a pure merge ([SyncMerge]); the transport is not
 * wired here, so nothing leaves the phone.
 */
enum class Kind { NOTE, REMINDER, TIMER }

data class Item(
    val id: String,
    val kind: Kind,
    val text: String,
    /** When a reminder or timer fires (epoch ms); null for notes. */
    val dueAtMs: Long?,
    val updatedAtMs: Long,
    val deviceId: String,
    val done: Boolean = false,
    /** Tombstone: kept so a delete wins over an older copy on the other side. */
    val deleted: Boolean = false,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("id", id).put("kind", kind.name).put("text", text)
        .put("dueAtMs", dueAtMs ?: JSONObject.NULL)
        .put("updatedAtMs", updatedAtMs).put("deviceId", deviceId)
        .put("done", done).put("deleted", deleted)

    companion object {
        fun fromJson(o: JSONObject): Item? = try {
            Item(
                id = o.getString("id"),
                kind = Kind.valueOf(o.getString("kind")),
                text = o.getString("text"),
                dueAtMs = if (o.isNull("dueAtMs")) null else o.getLong("dueAtMs"),
                updatedAtMs = o.getLong("updatedAtMs"),
                deviceId = o.getString("deviceId"),
                done = o.optBoolean("done", false),
                deleted = o.optBoolean("deleted", false),
            )
        } catch (e: Exception) {
            null
        }
    }
}

/** Where the JSON text lives (a private file on the phone; a string in tests). */
interface Storage {
    fun read(): String?
    fun write(text: String)
}

/** Sets and cancels the wake-up for one item. */
interface AlarmScheduler {
    fun schedule(id: String, atMs: Long)
    fun cancel(id: String)
}

/** Exact alarms need a permission the owner grants in system settings (Android 12+); without it a slightly inexact alarm is the honest fallback. */
object AlarmMode {
    enum class Mode { EXACT, INEXACT }

    fun choose(canScheduleExact: Boolean): Mode = if (canScheduleExact) Mode.EXACT else Mode.INEXACT
}

class OfflineStore(
    private val storage: Storage,
    private val scheduler: AlarmScheduler,
    private val deviceId: String,
    private val clock: () -> Long,
    private val newId: () -> String = { UUID.randomUUID().toString() },
) {
    private val items = LinkedHashMap<String, Item>()

    init {
        load()
    }

    private fun load() {
        val text = storage.read() ?: return
        try {
            val arr = JSONArray(text)
            for (i in 0 until arr.length()) Item.fromJson(arr.getJSONObject(i))?.let { items[it.id] = it }
        } catch (e: Exception) {
            // A damaged file starts empty rather than crashing the app.
            items.clear()
        }
    }

    private fun save() {
        val arr = JSONArray()
        items.values.forEach { arr.put(it.toJson()) }
        storage.write(arr.toString())
    }

    /** Live items, newest first. */
    fun list(kind: Kind? = null): List<Item> =
        items.values.filter { !it.deleted && (kind == null || it.kind == kind) }.sortedByDescending { it.updatedAtMs }

    /** Everything including tombstones: what sync sends. */
    fun all(): List<Item> = items.values.toList()

    fun addNote(text: String): Item = put(Item(newId(), Kind.NOTE, text.trim(), null, clock(), deviceId))

    fun addReminder(text: String, atMs: Long): Item {
        val it = put(Item(newId(), Kind.REMINDER, text.trim(), atMs, clock(), deviceId))
        scheduler.schedule(it.id, atMs)
        return it
    }

    fun startTimer(text: String, durationMs: Long): Item {
        require(durationMs > 0) { "duration must be positive" }
        val at = clock() + durationMs
        val it = put(Item(newId(), Kind.TIMER, text.trim(), at, clock(), deviceId))
        scheduler.schedule(it.id, at)
        return it
    }

    fun editText(id: String, text: String): Item? = update(id) { it.copy(text = text.trim()) }

    /** The alarm fired or the owner dismissed it. */
    fun complete(id: String): Item? {
        scheduler.cancel(id)
        return update(id) { it.copy(done = true) }
    }

    fun delete(id: String): Item? {
        scheduler.cancel(id)
        return update(id) { it.copy(deleted = true) }
    }

    private fun update(id: String, f: (Item) -> Item): Item? {
        val cur = items[id]?.takeIf { !it.deleted } ?: return null
        return put(f(cur).copy(updatedAtMs = clock(), deviceId = deviceId))
    }

    private fun put(item: Item): Item {
        items[item.id] = item
        save()
        return item
    }

    /**
     * After boot or an app update the system forgets alarms: set them again. Items already past due are returned (not scheduled) so the caller
     * can tell the owner now. Timers and reminders only; done and deleted ones are skipped.
     */
    fun rescheduleAll(): List<Item> {
        val now = clock()
        val overdue = ArrayList<Item>()
        for (it in items.values) {
            val due = it.dueAtMs ?: continue
            if (it.deleted || it.done || it.kind == Kind.NOTE) continue
            if (due <= now) overdue.add(it) else scheduler.schedule(it.id, due)
        }
        return overdue
    }

    /** Take the merged result of a sync and make the alarms agree with it. */
    fun applyMerged(merged: List<Item>) {
        for (m in merged) {
            val old = items[m.id]
            items[m.id] = m
            val due = m.dueAtMs
            if (m.deleted || m.done || due == null) scheduler.cancel(m.id)
            else if (old != m && m.kind != Kind.NOTE) scheduler.schedule(m.id, due)
        }
        save()
    }
}

/** Two copies of the same item: the later edit wins; on a tie a delete wins, then the larger device id (so both sides pick the same one). */
object SyncMerge {
    fun pick(a: Item, b: Item): Item = when {
        a.updatedAtMs != b.updatedAtMs -> if (a.updatedAtMs > b.updatedAtMs) a else b
        a.deleted != b.deleted -> if (a.deleted) a else b
        else -> if (a.deviceId >= b.deviceId) a else b
    }

    /** Union by id. Commutative and idempotent, so repeated or reordered syncs converge. */
    fun merge(local: List<Item>, remote: List<Item>): List<Item> {
        val out = LinkedHashMap<String, Item>()
        for (i in local) out[i.id] = i
        for (r in remote) out[r.id] = out[r.id]?.let { pick(it, r) } ?: r
        return out.values.toList()
    }

    /** What the other side lacks or has older: the items to push after merging. */
    fun toPush(merged: List<Item>, remote: List<Item>): List<Item> {
        val byId = remote.associateBy { it.id }
        return merged.filter { byId[it.id] != it }
    }
}
