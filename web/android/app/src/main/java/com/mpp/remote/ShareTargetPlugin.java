package com.mpp.remote;

import android.content.Context;
import android.content.SharedPreferences;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "ShareTarget")
public class ShareTargetPlugin extends Plugin {
    private static final String STORE_NAME = "mpp_share_target";
    private static final String PENDING_TEXT = "pending_text";

    public void acceptText(String text) {
        String normalized = text == null ? "" : text.trim();
        if (normalized.isEmpty()) return;
        preferences().edit().putString(PENDING_TEXT, normalized).apply();
        JSObject event = new JSObject();
        event.put("text", normalized);
        notifyListeners("shareReceived", event, true);
    }

    @PluginMethod
    public void getPendingShare(PluginCall call) {
        String text = preferences().getString(PENDING_TEXT, "");
        preferences().edit().remove(PENDING_TEXT).apply();
        JSObject result = new JSObject();
        result.put("text", text);
        call.resolve(result);
    }

    private SharedPreferences preferences() {
        return getContext().getSharedPreferences(STORE_NAME, Context.MODE_PRIVATE);
    }
}
