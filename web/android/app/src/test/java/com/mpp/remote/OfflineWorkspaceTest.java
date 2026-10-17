package com.mpp.remote;

import static org.junit.Assert.*;

import android.content.Context;
import androidx.appcompat.app.AppCompatActivity;
import com.getcapacitor.JSObject;
import com.getcapacitor.PluginCall;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.annotation.Config;

import java.io.File;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.MessageDigest;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Runs the actual plugin, SQLite databases, filesystem and HTTP sync without a device. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 28, manifest = Config.NONE)
public class OfflineWorkspaceTest {
    private final Context context = RuntimeEnvironment.getApplication();
    private OfflineArchivePlugin plugin;
    private ServerSocket server;
    private ExecutorService httpThreads;
    private String url;
    private final AtomicReference<String> workspace = new AtomicReference<>("/资料/A");
    private volatile CountDownLatch changeStarted;
    private volatile CountDownLatch releaseChange;
    private static final String ID = "8e4969ed-508d-4510-a7de-94dcd9bf70f2";

    @Before public void setUp() throws Exception {
        plugin = new OfflineArchivePlugin() {
            @Override public Context getContext() { return context; }
            @Override public AppCompatActivity getActivity() { return null; }
        };
        plugin.load();
        server = new ServerSocket();
        server.bind(new InetSocketAddress("localhost", 0));
        httpThreads = Executors.newCachedThreadPool();
        httpThreads.execute(() -> {
            while (!server.isClosed()) {
                try {
                    Socket socket = server.accept();
                    httpThreads.execute(() -> { try { respond(socket); } catch (IOException ignored) { } });
                } catch (IOException ignored) { }
            }
        });
        url = "http://localhost:" + server.getLocalPort();
    }

    private void respond(Socket socket) throws IOException {
        try (socket) {
            BufferedReader input = new BufferedReader(new InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8));
            String first = input.readLine();
            if (first == null) return;
            URI request = URI.create(first.split(" ")[1]);
            String header;
            while ((header = input.readLine()) != null && !header.isEmpty()) { }
            String current = workspace.get();
            String path = request.getPath();
            String body;
            if (path.endsWith("status")) {
                body = new JSObject().put("workspace_id", current).toString();
            } else if (path.endsWith("changes")) {
                if (changeStarted != null) changeStarted.countDown();
                if (releaseChange != null) {
                    try { releaseChange.await(10, TimeUnit.SECONDS); } catch (InterruptedException ignored) { }
                }
                long revision = current.endsWith("A") ? 900 : 1;
                String query = request.getQuery();
                String changes = query.contains("cursor=" + revision + "&") ? "[]"
                    : "[{\"operation\":\"upsert\",\"archive_id\":\"" + ID + "\",\"revision\":" + revision
                        + ",\"archive\":{\"title\":\"" + current + "\",\"path\":\"/archive\"}}]";
                body = "{\"workspace_id\":\"" + current + "\",\"changes\":" + changes
                    + ",\"next_cursor\":" + revision + ",\"has_more\":false}";
            } else if (path.endsWith("summary.md")) {
                body = current;
            } else {
                JSObject file = new JSObject().put("relative_path", "summary.md").put("sha256", checksum(current))
                    .put("size", current.getBytes(StandardCharsets.UTF_8).length).put("mime", "text/markdown");
                body = new JSObject().put("workspace_id", current).put("files", new org.json.JSONArray().put(file)).toString();
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            socket.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
            socket.getOutputStream().write(bytes);
        }
    }

    @After public void tearDown() throws Exception {
        if (releaseChange != null) releaseChange.countDown();
        server.close();
        httpThreads.shutdownNow();
        plugin.handleOnDestroy();
    }

    @Test public void isolatesAndRestoresDatabaseCursorAndFilesAcrossServerAndWorkspace() throws Exception {
        select(url, "/资料/A");
        assertEquals(900, sync(url, "/资料/A").getInt("cursor"));
        assertEquals("/资料/A", text("/资料/A"));
        String legacyContent = "original offline backup";
        File legacy = new File(context.getFilesDir(), "offline_archives/legacy.txt");
        legacy.getParentFile().mkdirs();
        Files.write(legacy.toPath(), legacyContent.getBytes(StandardCharsets.UTF_8));

        JSObject otherServer = select(url + "/other", "/资料/A");
        assertEquals(0, otherServer.getInt("cursor"));
        assertEquals(0, otherServer.getInt("archiveCount"));

        workspace.set("/资料/B");
        assertEquals(0, select(url, "/资料/B").getInt("cursor"));
        assertEquals(1, sync(url, "/资料/B").getInt("cursor"));
        assertEquals("/资料/B", text("/资料/B"));
        assertEquals(900, select(url, "/资料/A").getInt("cursor"));
        assertEquals("/资料/A", text("/资料/A"));
        Call archives = call(url, "/资料/A");
        plugin.listArchives(archives);
        assertEquals("/资料/A", archives.result().getJSONArray("archives").getJSONObject(0).getString("title"));
        assertEquals(legacyContent, new String(Files.readAllBytes(legacy.toPath()), StandardCharsets.UTF_8));
    }

    @Test public void rejectsStaleReadsAndDoesNotCommitDelayedSyncAfterSwitch() throws Exception {
        select(url, "/资料/A");
        changeStarted = new CountDownLatch(1);
        releaseChange = new CountDownLatch(1);
        Call oldSync = call(url, "/资料/A");
        plugin.sync(oldSync);
        assertTrue(changeStarted.await(5, TimeUnit.SECONDS));
        Call next = call(url, "/资料/B");
        java.lang.reflect.Field epoch = OfflineArchivePlugin.class.getDeclaredField("generation");
        epoch.setAccessible(true);
        long before = epoch.getLong(plugin);
        CompletableFuture<Void> switchStarted = CompletableFuture.runAsync(() -> plugin.selectWorkspace(next));
        // selectWorkspace invalidates the generation before waiting for HTTP teardown.
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (epoch.getLong(plugin) == before && System.nanoTime() < deadline) Thread.yield();
        assertNotEquals(before, epoch.getLong(plugin));
        releaseChange.countDown();
        switchStarted.get(5, TimeUnit.SECONDS);
        assertEquals(0, next.result().getInt("cursor"));
        assertTrue(oldSync.error().contains("取消"));
        Call staleRead = call(url, "/资料/A");
        plugin.listArchives(staleRead);
        assertTrue(staleRead.error().contains("已切换"));
        assertEquals(0, select(url, "/资料/A").getInt("archiveCount"));
    }

    @Test public void serverWorkspaceChangeRejectsSyncWithoutAdvancingCursor() throws Exception {
        select(url, "/资料/A");
        workspace.set("/资料/B");
        Call sync = call(url, "/资料/A");
        plugin.sync(sync);
        assertTrue(sync.error().contains("资料库已切换"));
        Call status = call(url, "/资料/A");
        plugin.getStatus(status);
        assertEquals(0, status.result().getInt("cursor"));
    }

    private JSObject select(String server, String workspace) throws Exception {
        Call call = call(server, workspace);
        plugin.selectWorkspace(call);
        return call.result();
    }
    private JSObject sync(String server, String workspace) throws Exception {
        Call call = call(server, workspace);
        plugin.sync(call);
        return call.result();
    }
    private Call call(String server, String workspace) {
        return new Call(new JSObject().put("serverUrl", server).put("workspaceId", workspace).put("token", "test"));
    }
    private String text(String workspace) throws Exception {
        Call call = new Call(new JSObject().put("serverUrl", url).put("workspaceId", workspace)
            .put("archiveId", ID).put("relativePath", "summary.md"));
        plugin.readText(call);
        return call.result().getString("content");
    }
    private static String checksum(String text) {
        try {
            StringBuilder result = new StringBuilder();
            for (byte value : MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8))) {
                result.append(String.format(java.util.Locale.ROOT, "%02x", value));
            }
            return result.toString();
        } catch (Exception error) { throw new AssertionError(error); }
    }
    private static final class Call extends PluginCall {
        final CompletableFuture<JSObject> completed = new CompletableFuture<>();
        Call(JSObject data) { super(null, "OfflineArchive", "test", "test", data); }
        @Override public void resolve(JSObject result) { completed.complete(result); }
        @Override public void reject(String message, String code, Exception error, JSObject data) {
            completed.completeExceptionally(new IllegalStateException(message, error));
        }
        JSObject result() throws Exception { return completed.get(10, TimeUnit.SECONDS); }
        String error() throws Exception {
            try { result(); fail("Expected rejection"); return ""; }
            catch (java.util.concurrent.ExecutionException error) { return error.getCause().getMessage(); }
        }
    }
}
