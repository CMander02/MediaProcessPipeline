import { Chat } from "@/components/Chat"

function App() {
  return (
    <main className="min-h-screen bg-background">
      <div className="container mx-auto py-8">
        <h1 className="text-3xl font-bold mb-8 text-center">AI Chat</h1>
        <Chat />
      </div>
    </main>
  )
}

export default App
