package com.mpp.remote;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import androidx.annotation.RequiresApi;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

@CapacitorPlugin(name = "FileDownload")
public class FileDownloadPlugin extends Plugin {
    @PluginMethod
    public void saveText(PluginCall call) {
        String filename = sanitizeFilename(call.getString("filename", "MPP-export.txt"));
        String content = call.getString("content", "");
        try {
            String uri = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
                ? saveWithMediaStore(filename, content)
                : saveToAppDownloads(filename, content);
            JSObject result = new JSObject();
            result.put("uri", uri);
            call.resolve(result);
        } catch (Exception error) {
            call.reject("文件保存失败", error);
        }
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private String saveWithMediaStore(String filename, String content) throws Exception {
        ContentResolver resolver = getContext().getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.Downloads.DISPLAY_NAME, filename);
        values.put(MediaStore.Downloads.MIME_TYPE, "text/plain");
        values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/MPP");
        values.put(MediaStore.Downloads.IS_PENDING, 1);
        Uri uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
        if (uri == null) throw new IllegalStateException("无法创建下载文件");
        try {
            try (OutputStream stream = resolver.openOutputStream(uri)) {
                if (stream == null) throw new IllegalStateException("无法写入下载文件");
                stream.write(content.getBytes(StandardCharsets.UTF_8));
            }
            ContentValues complete = new ContentValues();
            complete.put(MediaStore.Downloads.IS_PENDING, 0);
            resolver.update(uri, complete, null, null);
            return uri.toString();
        } catch (Exception error) {
            resolver.delete(uri, null, null);
            throw error;
        }
    }

    private String saveToAppDownloads(String filename, String content) throws Exception {
        File downloads = getContext().getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
        if (downloads == null) throw new IllegalStateException("下载目录不可用");
        File root = new File(downloads, "MPP");
        if (!root.exists() && !root.mkdirs()) throw new IllegalStateException("无法创建下载目录");
        File target = new File(root, filename);
        try (OutputStream stream = new FileOutputStream(target)) {
            stream.write(content.getBytes(StandardCharsets.UTF_8));
        }
        return Uri.fromFile(target).toString();
    }

    private String sanitizeFilename(String filename) {
        String safe = filename.replaceAll("[\\\\/:*?\"<>|]", "_").trim();
        return safe.isEmpty() ? "MPP-export.txt" : safe;
    }
}
