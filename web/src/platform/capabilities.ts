import type { Capabilities } from "@/lib/api"

export function applyAndroidCapabilities(capabilities: Capabilities): Capabilities {
  return {
    ...capabilities,
    browser_file_upload: false,
    browser_folder_upload: false,
    filesystem_browse: false,
    local_path_submission: false,
    open_local_folder: false,
    archive_mutation: false,
  }
}
