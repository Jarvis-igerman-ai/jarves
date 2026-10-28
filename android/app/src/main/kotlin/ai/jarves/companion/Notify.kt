package ai.jarves.companion

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import java.util.concurrent.TimeUnit

/**
 * Notifications (A3b), poll first: once the owner turns this on, Android runs a small check about every 15 minutes (the system's minimum). It asks
 * the paired PC how many approvals are waiting and, when that number GOES UP, shows one generic notification with the COUNT only: never a title,
 * a command or any content (a lock screen must not learn what JARVES is doing). No push service, no account, no foreground service; the
 * check is off until the owner turns it on, and off again on unpair.
 */
const val WORK_NAME = "jarves_waiting_check"
const val CHANNEL_ID = "approvals"
const val NOTIFY_ID = 4201

/** Pure rule: notify only when more approvals wait than the last time the owner was told. */
object NotifyPolicy {
    fun shouldNotify(count: Int, lastTold: Int): Boolean = count > 0 && count > lastTold

    /** What to remember as "told": 0 when nothing waits (so the next one notifies again), else the count we have now seen. */
    fun nextTold(count: Int, lastTold: Int): Int = maxOf(count, 0)

    sealed interface Step {
        data object Skip : Step
        data object Stop : Step
        data class Use(val count: Int) : Step
    }

    /** What the worker does with the PC's answer. [stillOn] is looked up AFTER the network call (switch on, session present, work not cancelled). */
    fun step(answer: Waiting, stillOn: Boolean): Step = when (answer) {
        Waiting.Revoked -> Step.Stop
        Waiting.Unknown -> Step.Skip
        is Waiting.Count -> if (stillOn) Step.Use(answer.n) else Step.Skip
    }

    fun text(count: Int): Int = if (count == 1) R.string.notify_one else R.string.notify_many
}

class NotifyPrefs(private val context: Context) {
    private fun p() = context.getSharedPreferences("jarves_notify", Context.MODE_PRIVATE)
    var enabled: Boolean
        get() = p().getBoolean("enabled", false)
        set(v) {
            p().edit().putBoolean("enabled", v).apply()
        }
    var lastTold: Int
        get() = p().getInt("told", 0)
        set(v) {
            p().edit().putInt("told", v).apply()
        }
}

object NotifyWork {
    fun start(context: Context) {
        val req = PeriodicWorkRequestBuilder<WaitingWorker>(15, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).setRequiresBatteryNotLow(true).build())
            .build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, req)
    }

    fun stop(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
        (context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(NOTIFY_ID)
        NotifyPrefs(context).lastTold = 0
    }

    fun permissionGranted(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
}

class WaitingWorker(private val ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val prefs = NotifyPrefs(ctx)
        if (!prefs.enabled || !NotifyWork.permissionGranted(ctx)) return Result.success()
        val s = SessionStore(ctx).load() ?: return Result.success()                      // not paired (any more): nothing to ask
        val answer = try {
            CompanionClient(s.base, PinnedTransport(s.pin)).waiting(s.token)
        } catch (e: Exception) {
            Waiting.Unknown
        }
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        // The call above can take many seconds: the owner may have switched this off or unpaired meanwhile. Look again before touching anything.
        val stillOn = !isStopped && prefs.enabled && SessionStore(ctx).load() != null
        val count = when (val step = NotifyPolicy.step(answer, stillOn)) {
            NotifyPolicy.Step.Skip -> return Result.success()                           // the PC did not answer, or the owner switched off meanwhile
            NotifyPolicy.Step.Stop -> {                                                 // this phone was removed: no more checks, no stale notification
                NotifyWork.stop(ctx)
                prefs.enabled = false
                return Result.success()
            }
            is NotifyPolicy.Step.Use -> step.count
        }
        if (NotifyPolicy.shouldNotify(count, prefs.lastTold)) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ID, ctx.getString(R.string.notify_channel), NotificationManager.IMPORTANCE_DEFAULT))
            val open = PendingIntent.getActivity(ctx, 0, Intent(ctx, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val n = NotificationCompat.Builder(ctx, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(ctx.getString(R.string.app_name))
                .setContentText(if (count == 1) ctx.getString(R.string.notify_one) else ctx.getString(R.string.notify_many, count))
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setPublicVersion(NotificationCompat.Builder(ctx, CHANNEL_ID).setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle(ctx.getString(R.string.app_name)).build())
                .setContentIntent(open)
                .setAutoCancel(true)
                .build()
            nm.notify(NOTIFY_ID, n)
        } else if (count == 0) {
            nm.cancel(NOTIFY_ID)
        }
        prefs.lastTold = NotifyPolicy.nextTold(count, prefs.lastTold)
        return Result.success()
    }
}

/** The opt-in switch (off by default). Turning it on asks Android for the notification permission first on Android 13+. */
@Composable
fun NotifyCard(enabled: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(stringResource(R.string.notify_switch), Modifier.weight(1f).padding(end = 12.dp))
        Switch(enabled, onChange)
    }
    Text(stringResource(R.string.notify_note), style = MaterialTheme.typography.bodySmall)
}
