import { defineConfig, presetUno, presetAttributify, presetIcons } from "unocss"

export default defineConfig({
  presets: [
    presetUno(),
    presetAttributify(),
    presetIcons({
      scale: 1.2,
      cdn: "https://esm.sh/",
    }),
  ],
  shortcuts: {
    "flex-center": "flex items-center justify-center",
    "flex-between": "flex items-center justify-between",
  },
  theme: {
    colors: {
      primary: "var(--el-color-primary)",
      success: "var(--el-color-success)",
      warning: "var(--el-color-warning)",
      danger: "var(--el-color-danger)",
      info: "var(--el-color-info)",
    },
  },
})
