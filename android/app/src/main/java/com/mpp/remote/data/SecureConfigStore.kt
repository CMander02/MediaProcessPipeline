package com.mpp.remote.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class SecureConfigStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun load(): ServerConfig {
        val encryptedToken = preferences.getString(KEY_TOKEN, "").orEmpty()
        val token = if (encryptedToken.isBlank()) {
            ""
        } else {
            runCatching { decrypt(encryptedToken) }
                .getOrElse {
                    preferences.edit().remove(KEY_TOKEN).apply()
                    ""
                }
        }

        return ServerConfig(
            baseUrl = preferences.getString(KEY_BASE_URL, "").orEmpty(),
            apiToken = token,
        )
    }

    fun save(config: ServerConfig) {
        val encryptedToken = if (config.apiToken.isBlank()) "" else encrypt(config.apiToken)
        preferences.edit()
            .putString(KEY_BASE_URL, config.baseUrl)
            .putString(KEY_TOKEN, encryptedToken)
            .apply()
    }

    fun loadThemeMode(): ThemeMode =
        ThemeMode.fromStoredValue(preferences.getString(KEY_THEME_MODE, null))

    fun saveThemeMode(themeMode: ThemeMode) {
        preferences.edit()
            .putString(KEY_THEME_MODE, themeMode.storedValue)
            .apply()
    }

    fun loadProcessingTarget(): ProcessingTarget =
        ProcessingTarget.fromStoredValue(preferences.getString(KEY_PROCESSING_TARGET, null))

    fun saveProcessingTarget(target: ProcessingTarget) {
        preferences.edit()
            .putString(KEY_PROCESSING_TARGET, target.storedValue)
            .apply()
    }

    private fun encrypt(value: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val encrypted = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        val payload = ByteArray(cipher.iv.size + encrypted.size)
        cipher.iv.copyInto(payload, destinationOffset = 0)
        encrypted.copyInto(payload, destinationOffset = cipher.iv.size)
        return Base64.encodeToString(payload, Base64.NO_WRAP)
    }

    private fun decrypt(value: String): String {
        val payload = Base64.decode(value, Base64.NO_WRAP)
        require(payload.size > IV_SIZE_BYTES) { "Invalid encrypted token" }
        val iv = payload.copyOfRange(0, IV_SIZE_BYTES)
        val encrypted = payload.copyOfRange(IV_SIZE_BYTES, payload.size)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(TAG_SIZE_BITS, iv))
        return cipher.doFinal(encrypted).toString(Charsets.UTF_8)
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEY_STORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEY_STORE)
        val specification = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true)
            .build()
        generator.init(specification)
        return generator.generateKey()
    }

    private companion object {
        const val PREFERENCES_NAME = "mpp_remote_secure_config"
        const val KEY_BASE_URL = "base_url"
        const val KEY_TOKEN = "api_token"
        const val KEY_THEME_MODE = "theme_mode"
        const val KEY_PROCESSING_TARGET = "processing_target"
        const val KEY_ALIAS = "mpp_remote_api_token"
        const val ANDROID_KEY_STORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_SIZE_BYTES = 12
        const val TAG_SIZE_BITS = 128
    }
}
