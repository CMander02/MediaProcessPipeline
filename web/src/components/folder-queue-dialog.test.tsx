/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import "@testing-library/jest-dom/vitest"
import { afterEach, describe, expect, it, vi } from "vitest"

import { FolderQueueDialog } from "./folder-queue-dialog"
import { api } from "@/lib/api"
import { PREFERRED_WORKER_OPTION } from "@/lib/task-routing"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function mockFolderBrowser() {
  vi.spyOn(api.filesystem, "drives").mockResolvedValue({
    success: true,
    drives: [{ name: "C:", path: "C:\\", is_dir: true, size: null }],
  })
  vi.spyOn(api.filesystem, "browse").mockResolvedValue({
    success: true,
    path: "C:\\",
    items: [{
      name: "Media",
      path: "C:\\Media",
      is_dir: true,
      size: null,
    }],
  })
  vi.spyOn(api.filesystem, "scanFolder").mockResolvedValue({
    success: true,
    files: [
      { path: "C:\\Media\\one.mp4", name: "one.mp4", size: 1024 },
      { path: "C:\\Media\\two.mp3", name: "two.mp3", size: 2048 },
    ],
    count: 2,
  })
}

async function selectMediaFolder() {
  const folder = await screen.findByRole("button", { name: /Media/ })
  fireEvent.click(folder)
  await screen.findByText("one.mp4")
}

describe("FolderQueueDialog processing target guard", () => {
  it("shows a clear warning and blocks server submission for an EXE-local folder", async () => {
    mockFolderBrowser()
    const create = vi.spyOn(api.tasks, "create").mockResolvedValue({} as never)

    render(
      <FolderQueueDialog
        open
        onOpenChange={vi.fn()}
        options={{}}
        processingTarget="server"
        serverLocalPathsBlocked
      />,
    )
    await selectMediaFolder()

    expect(screen.getByRole("alert")).toHaveTextContent(
      "服务器无法访问此 EXE 上的文件夹",
    )
    expect(screen.getByRole("button", { name: "提交全部 2 个文件" })).toBeDisabled()
    expect(create).not.toHaveBeenCalled()
  })

  it("submits every folder entry to the selected concrete worker", async () => {
    mockFolderBrowser()
    const create = vi.spyOn(api.tasks, "create").mockResolvedValue({} as never)
    const onSubmitted = vi.fn()

    render(
      <FolderQueueDialog
        open
        onOpenChange={vi.fn()}
        options={{ force_asr: true }}
        processingTarget="worker:desktop-a"
        serverLocalPathsBlocked
        onSubmitted={onSubmitted}
      />,
    )
    await selectMediaFolder()
    fireEvent.click(screen.getByRole("button", { name: "提交全部 2 个文件" }))

    await waitFor(() => expect(create).toHaveBeenCalledTimes(2))
    expect(create).toHaveBeenNthCalledWith(
      1,
      "C:\\Media\\one.mp4",
      {
        force_asr: true,
        [PREFERRED_WORKER_OPTION]: "desktop-a",
      },
      {
        origin_client: "web",
        requested_executor: "exe",
      },
    )
    expect(create).toHaveBeenNthCalledWith(
      2,
      "C:\\Media\\two.mp3",
      {
        force_asr: true,
        [PREFERRED_WORKER_OPTION]: "desktop-a",
      },
      {
        origin_client: "web",
        requested_executor: "exe",
      },
    )
    expect(onSubmitted).toHaveBeenCalledOnce()
  })
})
