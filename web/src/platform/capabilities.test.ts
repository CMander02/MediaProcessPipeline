import { describe, expect, it } from "vitest"

import { applyAndroidCapabilities } from "@/platform/capabilities"
import type { Capabilities } from "@/lib/api"

const remoteCapabilities: Capabilities = {
  mode: "remote",
  authenticated: true,
  url_submission: true,
  browser_file_upload: true,
  browser_folder_upload: true,
  task_control: true,
  settings: true,
  filesystem_browse: true,
  local_path_submission: true,
  open_local_folder: true,
  archive_mutation: true,
}

describe("Android capability boundary", () => {
  it("keeps online work and hides host filesystem mutations", () => {
    expect(applyAndroidCapabilities(remoteCapabilities)).toEqual({
      ...remoteCapabilities,
      browser_file_upload: false,
      browser_folder_upload: false,
      filesystem_browse: false,
      local_path_submission: false,
      open_local_folder: false,
      archive_mutation: false,
    })
  })
})
