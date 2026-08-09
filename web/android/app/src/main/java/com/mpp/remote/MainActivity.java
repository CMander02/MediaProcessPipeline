package com.mpp.remote;

import android.content.Intent;
import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(SecureCredentialsPlugin.class);
        registerPlugin(ShareTargetPlugin.class);
        registerPlugin(FileDownloadPlugin.class);
        registerPlugin(OfflineArchivePlugin.class);
        super.onCreate(savedInstanceState);
        handleShareIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleShareIntent(intent);
    }

    private void handleShareIntent(Intent intent) {
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) return;
        CharSequence sharedText = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
        if (sharedText == null) return;
        ShareTargetPlugin plugin = (ShareTargetPlugin) getBridge().getPlugin("ShareTarget").getInstance();
        plugin.acceptText(sharedText.toString());
    }
}
