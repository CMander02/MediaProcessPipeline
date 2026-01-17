import { useState, useCallback } from "react"

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
}

interface UseChatApiOptions {
  api?: string
}

export function useChatApi(options: UseChatApiOptions = {}) {
  const { api = "/api/chat/stream" } = options
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim()) return

      const userMessage: Message = {
        id: crypto.randomUUID(),
        role: "user",
        content: content.trim(),
      }

      const newMessages = [...messages, userMessage]
      setMessages(newMessages)
      setInput("")
      setIsLoading(true)

      try {
        const response = await fetch(api, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            messages: newMessages.map((m) => ({
              role: m.role,
              content: m.content,
            })),
            stream: true,
          }),
        })

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`)
        }

        const reader = response.body?.getReader()
        if (!reader) throw new Error("No reader available")

        const assistantMessage: Message = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "",
        }

        setMessages([...newMessages, assistantMessage])

        const decoder = new TextDecoder()
        let buffer = ""

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split("\n")
          buffer = lines.pop() || ""

          for (const line of lines) {
            if (line.startsWith("data: ")) {
              const data = line.slice(6)
              if (data === "[DONE]") continue

              try {
                const parsed = JSON.parse(data)
                if (parsed.content) {
                  assistantMessage.content += parsed.content
                  setMessages((prev) =>
                    prev.map((m) =>
                      m.id === assistantMessage.id ? { ...assistantMessage } : m
                    )
                  )
                }
              } catch {
                // Ignore parse errors
              }
            }
          }
        }
      } catch (error) {
        console.error("Chat error:", error)
        const errorMessage: Message = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "An error occurred. Please try again.",
        }
        setMessages([...newMessages, errorMessage])
      } finally {
        setIsLoading(false)
      }
    },
    [api, messages]
  )

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault()
      sendMessage(input)
    },
    [input, sendMessage]
  )

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      setInput(e.target.value)
    },
    []
  )

  return {
    messages,
    input,
    isLoading,
    setInput,
    handleInputChange,
    handleSubmit,
    sendMessage,
  }
}
