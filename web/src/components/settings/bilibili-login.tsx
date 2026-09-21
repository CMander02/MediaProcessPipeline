import { useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"

export function BilibiliLogin({ onSuccess }: { onSuccess: () => Promise<void> }) {
  const [qr, setQr] = useState<{ session_id: string; image: string; expiresAt: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState("")
  const generation = useRef(0)

  useEffect(() => () => { generation.current += 1 }, [])

  useEffect(() => {
    if (!qr) return
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      if (Date.now() >= qr.expiresAt) {
        setQr(null)
        setMessage("二维码已过期，请重新获取")
        return
      }
      try {
        const result = await api.bilibili.pollQr(qr.session_id)
        if (disposed) return
        setMessage(result.message)
        if (result.state === "success" || result.state === "expired") {
          setQr(null)
          if (result.state === "success") await onSuccess()
          return
        }
      } catch (error) {
        if (disposed) return
        setMessage(error instanceof Error ? error.message : "登录检测失败，正在重试")
      }
      if (!disposed) timer = setTimeout(poll, 3000)
    }
    timer = setTimeout(poll, 3000)
    return () => { disposed = true; clearTimeout(timer) }
  }, [qr, onSuccess])

  const start = async () => {
    const current = ++generation.current
    setLoading(true)
    setQr(null)
    setMessage("")
    try {
      const result = await api.bilibili.generateQr()
      if (generation.current !== current) return
      setQr({ ...result, expiresAt: Date.now() + result.expires_in * 1000 })
      setMessage("请使用哔哩哔哩 App 扫码，并在手机上确认登录")
    } catch (error) {
      if (generation.current === current) setMessage(error instanceof Error ? error.message : "获取二维码失败")
    } finally {
      if (generation.current === current) setLoading(false)
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">扫码后自动保存登录凭据。未登录或登录失效时，公开视频自动使用 360P 下载。</p>
      <div className="flex gap-2">
        <Button type="button" size="sm" disabled={loading} onClick={() => void start()}>
          {loading ? "正在获取二维码…" : qr ? "刷新二维码" : "扫码登录"}
        </Button>
        {qr && <Button type="button" size="sm" variant="outline" onClick={() => { setQr(null); setMessage("") }}>取消</Button>}
      </div>
      {qr && <img src={qr.image} alt="哔哩哔哩登录二维码" width={208} height={208} className="bg-white rounded-md" />}
      {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
    </div>
  )
}
