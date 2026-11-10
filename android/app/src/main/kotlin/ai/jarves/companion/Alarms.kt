package ai.jarves.companion

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationCompat
import java.io.File

const val ALARM_CHANNEL = "reminders"
const val EXTRA_ITEM_ID = "item_id"
const val ACTION_ALARM = "ai.jarves.companion.ALARM"

/** Private file in the app's own storage (not backed up: allowBackup is off). */
class FileStorage(private val file: File) : Storage {
    override fun read(): String? = if (file.exists()) file.readText() else null
    override fun write(text: String) {
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(text)
        if (!tmp.renameTo(file)) file.writeText(text)
    }
}

fun offlineStore(ctx: Context): OfflineStore = OfflineStore(
    FileStorage(File(ctx.filesDir, "offline.json")),
    AndroidAlarmScheduler(ctx.applicationContext),
    deviceId = Settings.Secure.getString(ctx.contentResolver, Settings.Secure.ANDROID_ID) ?: "phone",
    clock = System::currentTimeMillis,
)

class AndroidAlarmScheduler(private val ctx: Context) : AlarmScheduler {
    private val am get() = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager

    private fun pending(id: String): PendingIntent = PendingIntent.getBroadcast(
        ctx, id.hashCode(),
        Intent(ctx, AlarmReceiver::class.java).setAction(ACTION_ALARM).putExtra(EXTRA_ITEM_ID, id),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    /** The owner's exact-alarm permission is checked on every call: it can be revoked at any time. */
    fun canExact(): Boolean = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()

    override fun schedule(id: String, atMs: Long) {
        val pi = pending(id)
        when (AlarmMode.choose(canExact())) {
            AlarmMode.Mode.EXACT -> try {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
            } catch (e: SecurityException) {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
            }
            AlarmMode.Mode.INEXACT -> am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, atMs, pi)
        }
    }

    override fun cancel(id: String) = am.cancel(pending(id))
}

/** Fires the reminder: marks it done and shows the text the owner wrote (their own words, on their own phone). */
class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val store = offlineStore(context)
        when (intent.action) {
            ACTION_ALARM -> {
                val id = intent.getStringExtra(EXTRA_ITEM_ID) ?: return
                val item = store.list().firstOrNull { it.id == id } ?: return
                store.complete(id)
                val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    nm.createNotificationChannel(NotificationChannel(ALARM_CHANNEL, "Reminders", NotificationManager.IMPORTANCE_HIGH))
                }
                try {
                    nm.notify(
                        id.hashCode(),
                        NotificationCompat.Builder(context, ALARM_CHANNEL)
                            .setSmallIcon(android.R.drawable.ic_popup_reminder)
                            .setContentText(item.text).setAutoCancel(true).build(),
                    )
                } catch (e: SecurityException) {
                    // Notifications not allowed: the item is still marked done and visible in the app.
                }
            }
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED -> store.rescheduleAll()
        }
    }
}
