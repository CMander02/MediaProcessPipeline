package com.mpp.remote;

import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.net.Uri;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

@CapacitorPlugin(name = "OfflineArchive")
public class OfflineArchivePlugin extends Plugin {
    private static final int CHANGE_PAGE_SIZE = 100;
    private static final int DOWNLOAD_WORKERS = 3;
    private static final int BUFFER_SIZE = 64 * 1024;

    private ArchiveDatabase database;
    private volatile boolean syncing;
    private volatile int completedFiles;
    private volatile int totalFiles;
    private volatile long completedBytes;
    private volatile long totalBytes;
    private volatile String lastError = "";
    private ExecutorService syncExecutor;

    @Override
    public void load() {
        database = new ArchiveDatabase();
        syncExecutor = Executors.newSingleThreadExecutor();
    }

    @Override
    protected void handleOnDestroy() {
        if (syncExecutor != null) syncExecutor.shutdownNow();
        if (database != null) database.close();
    }

    @PluginMethod
    public void getStatus(PluginCall call) {
        call.resolve(statusObject());
    }

    @PluginMethod
    public void listArchives(PluginCall call) {
        try {
            JSObject result = new JSObject();
            result.put("archives", database.listArchives());
            call.resolve(result);
        } catch (Exception error) {
            call.reject("读取离线资料库失败", error);
        }
    }

    @PluginMethod
    public void getArchive(PluginCall call) {
        String archiveId = call.getString("archiveId", "");
        try {
            JSObject archive = database.getArchive(archiveId);
            if (archive == null) {
                call.reject("离线资料不存在");
                return;
            }
            call.resolve(new JSObject().put("archive", archive));
        } catch (Exception error) {
            call.reject("读取离线资料失败", error);
        }
    }

    @PluginMethod
    public void readText(PluginCall call) {
        String archiveId = call.getString("archiveId", "");
        String relativePath = call.getString("relativePath", "");
        try {
            File file = resolveDeclaredFile(archiveId, relativePath);
            if (file == null || !file.isFile()) {
                call.reject("离线文件不存在");
                return;
            }
            call.resolve(new JSObject().put("content", readUtf8(file)));
        } catch (Exception error) {
            call.reject("读取离线文件失败", error);
        }
    }

    @PluginMethod
    public void sync(PluginCall call) {
        if (syncing) {
            call.resolve(statusObject());
            return;
        }
        String serverUrl = trimServerUrl(call.getString("serverUrl", ""));
        String token = call.getString("token", "");
        if (serverUrl.isEmpty()) {
            call.reject("服务器地址为空");
            return;
        }
        syncing = true;
        lastError = "";
        completedFiles = 0;
        totalFiles = 0;
        completedBytes = 0;
        totalBytes = 0;
        emitProgress("正在读取同步索引");
        syncExecutor.execute(() -> {
            try {
                performSync(serverUrl, token);
                syncing = false;
                database.setMeta("last_sync", String.valueOf(System.currentTimeMillis()));
                database.setMeta("last_error", "");
                emitProgress("同步完成");
                call.resolve(statusObject());
            } catch (SyncException error) {
                syncing = false;
                lastError = error.getMessage();
                database.setMeta("last_error", lastError);
                emitProgress(error.status == 401 ? "访问令牌已失效" : "同步暂停，可稍后重试");
                JSObject details = statusObject();
                details.put("statusCode", error.status);
                call.reject(lastError, String.valueOf(error.status), error, details);
            } catch (Exception error) {
                syncing = false;
                lastError = readableError(error);
                database.setMeta("last_error", lastError);
                emitProgress("同步暂停，可稍后重试");
                call.reject(lastError, error);
            }
        });
    }

    @PluginMethod
    public void clear(PluginCall call) {
        if (syncing) {
            call.reject("同步进行中，请稍后再清理");
            return;
        }
        try {
            database.clearAll();
            deleteTree(archiveRoot());
            deleteTree(tempRoot());
            resetProgress();
            emitProgress("离线资料已清空");
            call.resolve(statusObject());
        } catch (Exception error) {
            call.reject("清空离线资料失败", error);
        }
    }

    @PluginMethod
    public void resetIndex(PluginCall call) {
        if (syncing) {
            call.reject("同步进行中，请稍后再重建索引");
            return;
        }
        try {
            database.clearIndex();
            resetProgress();
            emitProgress("本地索引已重置，连接服务器后将重新建立");
            call.resolve(statusObject());
        } catch (Exception error) {
            call.reject("重置本地索引失败", error);
        }
    }

    private void performSync(String serverUrl, String token) throws Exception {
        long cursor = database.getLongMeta("cursor", 0);
        boolean hasMore = true;
        while (hasMore) {
            JSONObject response = requestJson(
                serverUrl + "/api/sync/changes?cursor=" + cursor + "&limit=" + CHANGE_PAGE_SIZE,
                token
            );
            JSONArray changes = response.optJSONArray("changes");
            if (changes == null) changes = new JSONArray();
            for (int index = 0; index < changes.length(); index++) {
                JSONObject change = changes.getJSONObject(index);
                String operation = change.optString("operation");
                String archiveId = normalizeArchiveId(change.optString("archive_id"));
                long revision = change.optLong("revision");
                PreparedChange prepared = null;
                if ("delete".equals(operation)) {
                    prepared = PreparedChange.delete(archiveId, revision);
                } else if ("upsert".equals(operation)) {
                    JSONObject snapshot = change.optJSONObject("archive");
                    if (snapshot == null) throw new IOException("同步记录缺少归档内容");
                    try {
                        prepared = prepareArchive(serverUrl, token, archiveId, revision, snapshot);
                    } catch (SyncException error) {
                        if (error.status != 404) throw error;
                        // A newer tombstone can make an older upsert unavailable while paging.
                    }
                }
                List<PreparedChange> preparedChanges = new ArrayList<>();
                if (prepared != null) preparedChanges.add(prepared);
                database.applyChanges(preparedChanges, revision);
                if (prepared != null && prepared.deleted) {
                    deleteTree(archiveDirectory(prepared.archiveId));
                }
                cursor = revision;
                emitProgress("已保存离线资料");
            }

            long nextCursor = response.optLong("next_cursor", cursor);
            if (nextCursor > cursor) {
                database.applyChanges(new ArrayList<>(), nextCursor);
                cursor = nextCursor;
            }
            hasMore = response.optBoolean("has_more", false);
            emitProgress(hasMore ? "继续同步资料" : "正在完成同步");
        }
    }

    private PreparedChange prepareArchive(
        String serverUrl,
        String token,
        String archiveId,
        long revision,
        JSONObject snapshot
    ) throws Exception {
        JSONObject manifest = requestJson(
            serverUrl + "/api/sync/archives/" + Uri.encode(archiveId) + "/manifest",
            token
        );
        JSONArray files = manifest.optJSONArray("files");
        if (files == null) files = new JSONArray();
        List<FileRecord> records = new ArrayList<>();
        for (int index = 0; index < files.length(); index++) {
            JSONObject entry = files.getJSONObject(index);
            records.add(new FileRecord(
                entry.getString("relative_path"),
                entry.getString("sha256").toLowerCase(Locale.ROOT),
                entry.optLong("size", 0),
                entry.optString("mime", "application/octet-stream")
            ));
        }

        totalFiles += records.size();
        for (FileRecord record : records) totalBytes += record.size;
        emitProgress("正在同步「" + snapshot.optString("title", "资料") + "」");

        File finalDirectory = archiveDirectory(archiveId);
        File stageDirectory = new File(tempRoot(), archiveId + "/" + revision);
        ensureDirectory(stageDirectory, "无法创建同步临时目录");

        ExecutorService executor = Executors.newFixedThreadPool(DOWNLOAD_WORKERS);
        List<Future<?>> futures = new ArrayList<>();
        for (FileRecord record : records) {
            futures.add(executor.submit(() -> {
                stageFile(serverUrl, token, archiveId, finalDirectory, stageDirectory, record);
                return null;
            }));
        }
        executor.shutdown();
        Exception firstError = null;
        try {
            for (Future<?> future : futures) {
                try {
                    future.get();
                } catch (ExecutionException error) {
                    Throwable cause = error.getCause();
                    if (firstError == null) {
                        firstError = cause instanceof Exception
                            ? (Exception) cause
                            : new IOException("同步文件失败", cause);
                    }
                }
            }
        } finally {
            executor.shutdownNow();
        }
        if (firstError != null) throw firstError;

        replaceDirectory(stageDirectory, finalDirectory);
        JSONObject enriched = new JSONObject(snapshot.toString());
        enriched.put("archive_id", archiveId);
        enriched.put("revision", revision);
        enrichSnapshot(enriched, finalDirectory);
        return PreparedChange.upsert(archiveId, revision, enriched, records);
    }

    private void stageFile(
        String serverUrl,
        String token,
        String archiveId,
        File finalDirectory,
        File stageDirectory,
        FileRecord record
    ) throws Exception {
        File staged = safeChild(stageDirectory, record.relativePath);
        if (staged.isFile() && record.sha256.equals(sha256(staged))) {
            markFileComplete(record);
            return;
        }
        File existing = safeChild(finalDirectory, record.relativePath);
        if (existing.isFile() && record.sha256.equals(sha256(existing))) {
            copyVerified(existing, staged);
            markFileComplete(record);
            return;
        }
        downloadFile(
            serverUrl + "/api/sync/archives/" + Uri.encode(archiveId) + "/files/" + Uri.encode(record.relativePath, "/"),
            token,
            staged,
            record.sha256
        );
        markFileComplete(record);
    }

    private void markFileComplete(FileRecord record) {
        synchronized (this) {
            completedFiles += 1;
            completedBytes += record.size;
        }
        emitProgress("正在校验离线文件");
    }

    private void downloadFile(String url, String token, File destination, String expectedHash) throws Exception {
        File parent = destination.getParentFile();
        ensureDirectory(parent, "无法创建离线文件目录");
        File partial = new File(destination.getPath() + ".part");
        HttpURLConnection connection = openConnection(url, token);
        int status = connection.getResponseCode();
        if (status == 401) throw new SyncException(401, "访问令牌已失效");
        if (status < 200 || status >= 300) {
            throw new SyncException(status, readError(connection, "下载离线文件失败"));
        }
        try (
            InputStream input = new BufferedInputStream(connection.getInputStream());
            BufferedOutputStream output = new BufferedOutputStream(new FileOutputStream(partial))
        ) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int length;
            while ((length = input.read(buffer)) >= 0) output.write(buffer, 0, length);
        } finally {
            connection.disconnect();
        }
        if (!expectedHash.equals(sha256(partial))) {
            partial.delete();
            throw new IOException("离线文件校验失败");
        }
        if (destination.exists() && !destination.delete()) throw new IOException("无法替换离线文件");
        if (!partial.renameTo(destination)) {
            copyVerified(partial, destination);
            partial.delete();
        }
    }

    private JSONObject requestJson(String url, String token) throws Exception {
        HttpURLConnection connection = openConnection(url, token);
        int status = connection.getResponseCode();
        if (status == 401) throw new SyncException(401, "访问令牌已失效");
        if (status < 200 || status >= 300) {
            throw new SyncException(status, readError(connection, "同步服务返回错误"));
        }
        try (InputStream input = connection.getInputStream()) {
            return new JSONObject(readStream(input));
        } finally {
            connection.disconnect();
        }
    }

    private HttpURLConnection openConnection(String url, String token) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setUseCaches(false);
        connection.setConnectTimeout(15_000);
        connection.setReadTimeout(60_000);
        connection.setRequestProperty("Accept", "application/json, */*");
        connection.setRequestProperty("X-Requested-With", "mpp-android-sync");
        if (token != null && !token.isEmpty()) {
            connection.setRequestProperty("Authorization", "Bearer " + token);
        }
        return connection;
    }

    private void enrichSnapshot(JSONObject snapshot, File directory) {
        try {
            File metadataFile = new File(directory, "metadata.json");
            if (metadataFile.isFile()) snapshot.put("metadata", new JSONObject(readUtf8(metadataFile)));
        } catch (Exception ignored) {
            // The lite server snapshot remains usable when optional JSON is malformed.
        }
        try {
            File analysisFile = new File(directory, "analysis.json");
            if (analysisFile.isFile()) snapshot.put("analysis", new JSONObject(readUtf8(analysisFile)));
        } catch (Exception ignored) {
            // Analysis is optional.
        }
    }

    private JSObject statusObject() {
        JSObject status = new JSObject();
        status.put("syncing", syncing);
        status.put("cursor", database.getLongMeta("cursor", 0));
        status.put("archiveCount", database.countArchives());
        status.put("completedFiles", completedFiles);
        status.put("totalFiles", totalFiles);
        status.put("completedBytes", completedBytes);
        status.put("totalBytes", totalBytes);
        status.put("lastSync", database.getLongMeta("last_sync", 0));
        String persistedError = database.getMeta("last_error", "");
        status.put("lastError", lastError.isEmpty() ? persistedError : lastError);
        return status;
    }

    private void emitProgress(String message) {
        JSObject event = statusObject();
        event.put("message", message);
        if (getActivity() != null) {
            getActivity().runOnUiThread(() -> notifyListeners("syncProgress", event, true));
        }
    }

    private File resolveDeclaredFile(String archiveId, String relativePath) throws IOException {
        if (!database.hasFile(archiveId, relativePath)) return null;
        return safeChild(archiveDirectory(archiveId), relativePath);
    }

    private File archiveRoot() {
        return new File(getContext().getFilesDir(), "offline_archives");
    }

    private File tempRoot() {
        return new File(getContext().getFilesDir(), "offline_tmp");
    }

    private File archiveDirectory(String archiveId) {
        return new File(archiveRoot(), archiveId);
    }

    private static File safeChild(File root, String relativePath) throws IOException {
        if (relativePath == null || relativePath.isEmpty() || relativePath.contains("\\")) {
            throw new IOException("无效的离线文件路径");
        }
        File canonicalRoot = root.getCanonicalFile();
        File child = new File(canonicalRoot, relativePath).getCanonicalFile();
        String prefix = canonicalRoot.getPath() + File.separator;
        if (!child.getPath().startsWith(prefix)) throw new IOException("离线文件路径超出资料目录");
        return child;
    }

    private static void replaceDirectory(File staged, File destination) throws IOException {
        File parent = destination.getParentFile();
        ensureDirectory(parent, "无法创建离线资料目录");
        File backup = new File(destination.getPath() + ".previous");
        deleteTree(backup);
        if (destination.exists() && !destination.renameTo(backup)) {
            throw new IOException("无法准备离线资料更新");
        }
        if (!staged.renameTo(destination)) {
            if (backup.exists()) backup.renameTo(destination);
            throw new IOException("无法完成离线资料原子更新");
        }
        deleteTree(backup);
    }

    private static void copyVerified(File source, File destination) throws IOException {
        File parent = destination.getParentFile();
        ensureDirectory(parent, "无法创建离线目录");
        try (
            InputStream input = new BufferedInputStream(new FileInputStream(source));
            BufferedOutputStream output = new BufferedOutputStream(new FileOutputStream(destination))
        ) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int length;
            while ((length = input.read(buffer)) >= 0) output.write(buffer, 0, length);
        }
    }

    static void ensureDirectory(File directory, String errorMessage) throws IOException {
        if (directory == null || directory.isDirectory()) return;
        if (!directory.mkdirs() && !directory.isDirectory()) {
            throw new IOException(errorMessage);
        }
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(new FileInputStream(file))) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int length;
            while ((length = input.read(buffer)) >= 0) digest.update(buffer, 0, length);
        }
        StringBuilder value = new StringBuilder();
        for (byte item : digest.digest()) value.append(String.format(Locale.ROOT, "%02x", item));
        return value.toString();
    }

    private static String readUtf8(File file) throws IOException {
        try (InputStream input = new FileInputStream(file)) {
            return readStream(input);
        }
    }

    private static String readStream(InputStream input) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[BUFFER_SIZE];
        int length;
        while ((length = input.read(buffer)) >= 0) output.write(buffer, 0, length);
        return output.toString(StandardCharsets.UTF_8.name());
    }

    private static String readError(HttpURLConnection connection, String fallback) {
        try (InputStream error = connection.getErrorStream()) {
            if (error == null) return fallback + "（HTTP " + connection.getResponseCode() + "）";
            String body = readStream(error);
            try {
                JSONObject parsed = new JSONObject(body);
                return parsed.optString("detail", fallback);
            } catch (JSONException ignored) {
                return body.isEmpty() ? fallback : body;
            }
        } catch (IOException ignored) {
            return fallback;
        }
    }

    private static String trimServerUrl(String value) {
        if (value == null) return "";
        return value.trim().replaceAll("/+$", "");
    }

    private static String normalizeArchiveId(String value) throws IOException {
        try {
            return UUID.fromString(value).toString();
        } catch (IllegalArgumentException | NullPointerException error) {
            throw new IOException("同步记录包含无效归档标识", error);
        }
    }

    private static String readableError(Exception error) {
        String message = error.getMessage();
        return message == null || message.trim().isEmpty() ? "离线同步失败" : message;
    }

    private void resetProgress() {
        syncing = false;
        completedFiles = 0;
        totalFiles = 0;
        completedBytes = 0;
        totalBytes = 0;
        lastError = "";
    }

    private static void deleteTree(File file) {
        if (file == null || !file.exists()) return;
        File[] children = file.listFiles();
        if (children != null) for (File child : children) deleteTree(child);
        file.delete();
    }

    private static final class SyncException extends IOException {
        final int status;

        SyncException(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    private static final class FileRecord {
        final String relativePath;
        final String sha256;
        final long size;
        final String mime;

        FileRecord(String relativePath, String sha256, long size, String mime) {
            this.relativePath = relativePath;
            this.sha256 = sha256;
            this.size = size;
            this.mime = mime;
        }
    }

    private static final class PreparedChange {
        final String archiveId;
        final long revision;
        final boolean deleted;
        final JSONObject archive;
        final List<FileRecord> files;

        private PreparedChange(String archiveId, long revision, boolean deleted, JSONObject archive, List<FileRecord> files) {
            this.archiveId = archiveId;
            this.revision = revision;
            this.deleted = deleted;
            this.archive = archive;
            this.files = files;
        }

        static PreparedChange upsert(String archiveId, long revision, JSONObject archive, List<FileRecord> files) {
            return new PreparedChange(archiveId, revision, false, archive, files);
        }

        static PreparedChange delete(String archiveId, long revision) {
            return new PreparedChange(archiveId, revision, true, null, new ArrayList<>());
        }
    }

    private final class ArchiveDatabase extends SQLiteOpenHelper {
        ArchiveDatabase() {
            super(getContext(), "offline_archives.db", null, 1);
        }

        @Override
        public void onCreate(SQLiteDatabase db) {
            db.execSQL("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)");
            db.execSQL("CREATE TABLE archives (archive_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, json TEXT NOT NULL)");
            db.execSQL("CREATE TABLE files (archive_id TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL, PRIMARY KEY (archive_id, relative_path))");
            db.execSQL("CREATE INDEX files_archive_idx ON files(archive_id)");
        }

        @Override
        public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
            // Versioned migrations will be added when the local schema evolves.
        }

        synchronized String getMeta(String key, String fallback) {
            try (Cursor cursor = getReadableDatabase().rawQuery("SELECT value FROM meta WHERE key = ?", new String[]{key})) {
                return cursor.moveToFirst() ? cursor.getString(0) : fallback;
            }
        }

        synchronized long getLongMeta(String key, long fallback) {
            try {
                return Long.parseLong(getMeta(key, String.valueOf(fallback)));
            } catch (NumberFormatException ignored) {
                return fallback;
            }
        }

        synchronized void setMeta(String key, String value) {
            ContentValues values = new ContentValues();
            values.put("key", key);
            values.put("value", value);
            getWritableDatabase().insertWithOnConflict("meta", null, values, SQLiteDatabase.CONFLICT_REPLACE);
        }

        synchronized int countArchives() {
            try (Cursor cursor = getReadableDatabase().rawQuery("SELECT COUNT(*) FROM archives", null)) {
                return cursor.moveToFirst() ? cursor.getInt(0) : 0;
            }
        }

        synchronized boolean hasFile(String archiveId, String relativePath) {
            try (Cursor cursor = getReadableDatabase().rawQuery(
                "SELECT 1 FROM files WHERE archive_id = ? AND relative_path = ?",
                new String[]{archiveId, relativePath}
            )) {
                return cursor.moveToFirst();
            }
        }

        synchronized JSArray listArchives() throws JSONException {
            JSArray archives = new JSArray();
            try (Cursor cursor = getReadableDatabase().rawQuery(
                "SELECT archive_id, json FROM archives ORDER BY revision DESC",
                null
            )) {
                while (cursor.moveToNext()) archives.put(archiveObject(cursor.getString(0), cursor.getString(1)));
            }
            return archives;
        }

        synchronized JSObject getArchive(String archiveId) throws JSONException {
            try (Cursor cursor = getReadableDatabase().rawQuery(
                "SELECT json FROM archives WHERE archive_id = ?",
                new String[]{archiveId}
            )) {
                return cursor.moveToFirst() ? archiveObject(archiveId, cursor.getString(0)) : null;
            }
        }

        private JSObject archiveObject(String archiveId, String json) throws JSONException {
            JSObject archive = JSObject.fromJSONObject(new JSONObject(json));
            archive.put("server_path", archive.optString("path", ""));
            archive.put("path", "offline://" + archiveId);
            JSArray files = new JSArray();
            try (Cursor cursor = getReadableDatabase().rawQuery(
                "SELECT relative_path, sha256, size, mime FROM files WHERE archive_id = ? ORDER BY relative_path",
                new String[]{archiveId}
            )) {
                while (cursor.moveToNext()) {
                    String relativePath = cursor.getString(0);
                    File file = safeChild(archiveDirectory(archiveId), relativePath);
                    JSObject entry = new JSObject();
                    entry.put("relative_path", relativePath);
                    entry.put("sha256", cursor.getString(1));
                    entry.put("size", cursor.getLong(2));
                    entry.put("mime", cursor.getString(3));
                    entry.put("uri", Uri.fromFile(file).toString());
                    files.put(entry);
                }
            } catch (IOException error) {
                throw new JSONException(error.getMessage());
            }
            archive.put("offline_files", files);
            return archive;
        }

        synchronized void applyChanges(List<PreparedChange> changes, long cursor) throws JSONException {
            SQLiteDatabase db = getWritableDatabase();
            db.beginTransaction();
            try {
                for (PreparedChange change : changes) {
                    db.delete("files", "archive_id = ?", new String[]{change.archiveId});
                    if (change.deleted) {
                        db.delete("archives", "archive_id = ?", new String[]{change.archiveId});
                        continue;
                    }
                    ContentValues archiveValues = new ContentValues();
                    archiveValues.put("archive_id", change.archiveId);
                    archiveValues.put("revision", change.revision);
                    archiveValues.put("json", change.archive.toString());
                    db.insertWithOnConflict("archives", null, archiveValues, SQLiteDatabase.CONFLICT_REPLACE);
                    for (FileRecord file : change.files) {
                        ContentValues fileValues = new ContentValues();
                        fileValues.put("archive_id", change.archiveId);
                        fileValues.put("relative_path", file.relativePath);
                        fileValues.put("sha256", file.sha256);
                        fileValues.put("size", file.size);
                        fileValues.put("mime", file.mime);
                        db.insertOrThrow("files", null, fileValues);
                    }
                }
                ContentValues meta = new ContentValues();
                meta.put("key", "cursor");
                meta.put("value", String.valueOf(cursor));
                db.insertWithOnConflict("meta", null, meta, SQLiteDatabase.CONFLICT_REPLACE);
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
        }

        synchronized void clearIndex() {
            SQLiteDatabase db = getWritableDatabase();
            db.beginTransaction();
            try {
                db.delete("files", null, null);
                db.delete("archives", null, null);
                db.delete("meta", null, null);
                db.setTransactionSuccessful();
            } finally {
                db.endTransaction();
            }
        }

        synchronized void clearAll() {
            clearIndex();
        }
    }
}
