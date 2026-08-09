package com.mpp.remote;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

@CapacitorPlugin(name = "SecureCredentials")
public class SecureCredentialsPlugin extends Plugin {
    private static final String KEY_ALIAS = "mpp_api_token_key";
    private static final String STORE_NAME = "mpp_secure_credentials";
    private static final String CIPHER_TEXT = "token_cipher_text";
    private static final String CIPHER_IV = "token_cipher_iv";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";

    @PluginMethod
    public void getToken(PluginCall call) {
        try {
            SharedPreferences preferences = preferences();
            String encodedCipherText = preferences.getString(CIPHER_TEXT, "");
            String encodedIv = preferences.getString(CIPHER_IV, "");
            JSObject result = new JSObject();
            if (encodedCipherText.isEmpty() || encodedIv.isEmpty()) {
                result.put("token", "");
                call.resolve(result);
                return;
            }

            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(
                Cipher.DECRYPT_MODE,
                getOrCreateKey(),
                new GCMParameterSpec(128, Base64.decode(encodedIv, Base64.NO_WRAP))
            );
            byte[] clearText = cipher.doFinal(Base64.decode(encodedCipherText, Base64.NO_WRAP));
            result.put("token", new String(clearText, StandardCharsets.UTF_8));
            call.resolve(result);
        } catch (Exception error) {
            call.reject("无法读取安全凭据", error);
        }
    }

    @PluginMethod
    public void setToken(PluginCall call) {
        String token = call.getString("token", "");
        if (token.isEmpty()) {
            clearStoredToken();
            call.resolve();
            return;
        }
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey());
            byte[] encrypted = cipher.doFinal(token.getBytes(StandardCharsets.UTF_8));
            preferences().edit()
                .putString(CIPHER_TEXT, Base64.encodeToString(encrypted, Base64.NO_WRAP))
                .putString(CIPHER_IV, Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                .apply();
            call.resolve();
        } catch (Exception error) {
            call.reject("无法保存安全凭据", error);
        }
    }

    @PluginMethod
    public void clearToken(PluginCall call) {
        clearStoredToken();
        call.resolve();
    }

    private SharedPreferences preferences() {
        return getContext().getSharedPreferences(STORE_NAME, Context.MODE_PRIVATE);
    }

    private void clearStoredToken() {
        preferences().edit().remove(CIPHER_TEXT).remove(CIPHER_IV).apply();
    }

    private SecretKey getOrCreateKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        java.security.Key existing = keyStore.getKey(KEY_ALIAS, null);
        if (existing instanceof SecretKey) return (SecretKey) existing;

        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true)
            .build());
        return generator.generateKey();
    }
}
