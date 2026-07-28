package com.mpp.remote.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

class RecentTaskStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun load(): List<RemoteTask> {
        val raw = preferences.getString(KEY_TASKS, "[]").orEmpty()
        return runCatching {
            val array = JSONArray(raw)
            buildList {
                for (index in 0 until array.length()) {
                    add(array.getJSONObject(index).toRemoteTask())
                }
            }
        }.getOrDefault(emptyList())
    }

    fun upsert(task: RemoteTask): List<RemoteTask> {
        val updated = buildList {
            add(task)
            addAll(load().filterNot { it.id == task.id })
        }.take(MAX_TASKS)
        save(updated)
        return updated
    }

    fun save(tasks: List<RemoteTask>) {
        val array = JSONArray()
        tasks.take(MAX_TASKS).forEach { task ->
            array.put(
                JSONObject()
                    .put("id", task.id)
                    .put("status", task.status)
                    .put("source", task.source)
                    .put("progress", task.progress)
                    .put("message", task.message)
                    .put("created_at", task.createdAt)
                    .put("origin_client", task.originClient)
                    .put("requested_executor", task.requestedExecutor)
                    .put("assigned_executor", task.assignedExecutor),
            )
        }
        preferences.edit().putString(KEY_TASKS, array.toString()).apply()
    }

    private fun JSONObject.toRemoteTask(): RemoteTask = RemoteTask(
        id = optString("id"),
        status = optString("status"),
        source = optString("source"),
        progress = optDouble("progress", 0.0),
        message = optString("message"),
        createdAt = optString("created_at"),
        originClient = optString("origin_client"),
        requestedExecutor = optString("requested_executor"),
        assignedExecutor = optString("assigned_executor"),
    )

    private companion object {
        const val PREFERENCES_NAME = "mpp_remote_recent_tasks"
        const val KEY_TASKS = "tasks"
        const val MAX_TASKS = 30
    }
}
