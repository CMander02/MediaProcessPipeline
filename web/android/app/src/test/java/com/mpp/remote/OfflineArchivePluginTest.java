package com.mpp.remote;

import static org.junit.Assert.assertTrue;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Future;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class OfflineArchivePluginTest {
    @Rule
    public TemporaryFolder temporaryFolder = new TemporaryFolder();

    @Test
    public void ensureDirectoryAllowsConcurrentCreation() throws Exception {
        File directory = new File(temporaryFolder.getRoot(), "archive/images");
        int workerCount = 16;
        ExecutorService executor = Executors.newFixedThreadPool(workerCount);
        CountDownLatch ready = new CountDownLatch(workerCount);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<?>> futures = new ArrayList<>();

        for (int index = 0; index < workerCount; index++) {
            futures.add(executor.submit(() -> {
                ready.countDown();
                start.await();
                OfflineArchivePlugin.ensureDirectory(directory, "无法创建离线目录");
                return null;
            }));
        }

        ready.await();
        start.countDown();
        for (Future<?> future : futures) future.get();
        executor.shutdownNow();

        assertTrue(directory.isDirectory());
    }
}
