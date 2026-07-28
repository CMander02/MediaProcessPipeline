package com.mpp.remote.ui

import android.graphics.BitmapFactory
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import kotlin.math.max
import kotlin.math.min

internal const val MIN_ARCHIVE_IMAGE_SCALE = 1f
internal const val MAX_ARCHIVE_IMAGE_SCALE = 5f
internal const val DOUBLE_TAP_ARCHIVE_IMAGE_SCALE = 2.5f
internal const val ARCHIVE_GALLERY_IMAGE_MAX_EDGE = 512
internal const val ARCHIVE_PREVIEW_IMAGE_MAX_EDGE = 2048

internal data class ArchiveImagePanBounds(
    val horizontal: Float,
    val vertical: Float,
)

internal data class ArchiveImagePan(
    val x: Float,
    val y: Float,
)

internal fun archiveImagePanBounds(
    viewportWidth: Int,
    viewportHeight: Int,
    imageWidth: Int,
    imageHeight: Int,
    scale: Float,
): ArchiveImagePanBounds {
    if (
        viewportWidth <= 0 ||
        viewportHeight <= 0 ||
        imageWidth <= 0 ||
        imageHeight <= 0
    ) {
        return ArchiveImagePanBounds(0f, 0f)
    }
    val fitScale = min(
        viewportWidth.toFloat() / imageWidth,
        viewportHeight.toFloat() / imageHeight,
    )
    val scaledWidth = imageWidth * fitScale * scale.coerceAtLeast(MIN_ARCHIVE_IMAGE_SCALE)
    val scaledHeight = imageHeight * fitScale * scale.coerceAtLeast(MIN_ARCHIVE_IMAGE_SCALE)
    return ArchiveImagePanBounds(
        horizontal = max(0f, (scaledWidth - viewportWidth) / 2f),
        vertical = max(0f, (scaledHeight - viewportHeight) / 2f),
    )
}

internal fun clampArchiveImagePan(
    x: Float,
    y: Float,
    bounds: ArchiveImagePanBounds,
): ArchiveImagePan =
    ArchiveImagePan(
        x = x.coerceIn(-bounds.horizontal, bounds.horizontal),
        y = y.coerceIn(-bounds.vertical, bounds.vertical),
    )

internal fun updatedArchiveImageScale(current: Float, zoomChange: Float): Float =
    (current * zoomChange).coerceIn(
        MIN_ARCHIVE_IMAGE_SCALE,
        MAX_ARCHIVE_IMAGE_SCALE,
    )

internal fun doubleTapArchiveImageScale(current: Float): Float =
    if (current > MIN_ARCHIVE_IMAGE_SCALE + 0.05f) {
        MIN_ARCHIVE_IMAGE_SCALE
    } else {
        DOUBLE_TAP_ARCHIVE_IMAGE_SCALE
    }

internal fun calculateInSampleSize(
    width: Int,
    height: Int,
    maxDimension: Int,
): Int {
    if (width <= 0 || height <= 0 || maxDimension <= 0) return 1
    var sampleSize = 1
    while (width / sampleSize > maxDimension || height / sampleSize > maxDimension) {
        sampleSize *= 2
    }
    return sampleSize
}

internal fun decodeSampledImage(
    bytes: ByteArray,
    maxDimension: Int,
): ImageBitmap? {
    if (bytes.isEmpty()) return null
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
    val options = BitmapFactory.Options().apply {
        inSampleSize = calculateInSampleSize(
            width = bounds.outWidth,
            height = bounds.outHeight,
            maxDimension = maxDimension,
        )
        inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888
    }
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options)?.asImageBitmap()
}
