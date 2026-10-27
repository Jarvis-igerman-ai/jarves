package ai.jarves.companion

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder

/**
 * Push-to-talk recording: the microphone is open only between [start] and [stop], which the screen calls while the owner HOLDS the button
 * and the app is visible. The audio lives in memory only (never a file), is capped at [VOICE_MAX_SECONDS] and goes nowhere but the paired PC.
 */
class Recorder {
    @Volatile private var running = false
    private var thread: Thread? = null
    private var samples = ShortArray(0)
    @Volatile private var count = 0

    /** The caller has already checked the microphone permission. False = the microphone could not be opened. */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running) return true
        val min = AudioRecord.getMinBufferSize(VOICE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (min <= 0) return false
        val rec = try {
            AudioRecord(MediaRecorder.AudioSource.MIC, VOICE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, 8192))
        } catch (e: Exception) {
            return false
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            return false
        }
        samples = ShortArray(VOICE_RATE * VOICE_MAX_SECONDS)
        count = 0
        running = true
        thread = Thread {
            val buf = ShortArray(2048)
            try {
                rec.startRecording()
                while (running && count < samples.size) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n <= 0) break
                    val c = minOf(n, samples.size - count)
                    System.arraycopy(buf, 0, samples, count, c)
                    count += c
                }
            } catch (e: Exception) {
                // the recording simply ends; whatever was captured is returned by stop()
            } finally {
                try {
                    rec.stop()
                } catch (e: Exception) {
                    // already stopped
                }
                rec.release()
            }
        }.also { it.start() }
        return true
    }

    /** Closes the microphone and returns what was heard (an empty array when nothing was recorded). */
    fun stop(): ShortArray {
        running = false
        thread?.join(1500)
        thread = null
        val out = samples.copyOf(count)
        samples = ShortArray(0)
        count = 0
        return out
    }
}
