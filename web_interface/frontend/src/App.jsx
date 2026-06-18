import { useEffect, useRef, useState } from "react";
import ChatMessage from "./components/ChatMessage.jsx";
import MessageInput from "./components/MessageInput.jsx";
import SettingsPanel from "./components/SettingsPanel.jsx";
import * as api from "./api.js";

const EXEMPLOS = [
  "Quantos pedidos existem no banco?",
  "Quais as 5 categorias que mais venderam?",
  "Qual o ticket médio dos pedidos?",
];

export default function App() {
  const [messages, setMessages] = useState([]);
  const [settings, setSettings] = useState({
    hitl: true,
    rag: false,
    db_path: "",
    db_name: "",
    thread_id: "",
    awaiting: false,
  });
  const [dbs, setDbs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const endRef = useRef(null);

  // Hidrata estado + lista de bancos ao abrir.
  useEffect(() => {
    api.getState().then(setSettings).catch(() => {});
    api.listDbs().then((d) => setDbs(d.dbs || [])).catch(() => {});
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function handleSend(text) {
    setMessages((m) => [...m, { role: "user", text }]);
    setLoading(true);
    try {
      const saida = await api.sendMessage(text);
      setMessages((m) => [...m, { role: "assistant", saida }]);
      setSettings((s) => ({ ...s, awaiting: saida.status === "awaiting" }));
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: "assistant", saida: { status: "error", message: String(err.message) } },
      ]);
    } finally {
      setLoading(false);
    }
  }

  async function applySetting(promise, noteText) {
    try {
      const novo = await promise;
      setSettings(novo);
      if (noteText) {
        setMessages((m) => [...m, { role: "assistant", kind: "note", text: noteText }]);
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: "assistant", saida: { status: "error", message: String(err.message) } },
      ]);
    }
  }

  const onToggleHitl = (v) =>
    applySetting(
      api.setSettings({ hitl: v }),
      `Pedir esclarecimento: ${v ? "ligado" : "desligado"} (vale na próxima pergunta).`
    );
  const onToggleRag = (v) =>
    applySetting(
      api.setSettings({ rag: v }),
      `Conhecimento extra: ${v ? "ligado" : "desligado"} (vale na próxima pergunta).`
    );
  const onChangeDb = (db) =>
    applySetting(
      api.setSettings({ db: db }),
      `Banco trocado para "${basename(db)}" (vale na próxima pergunta).`
    );

  async function onNewChat() {
    try {
      const novo = await api.newChat();
      setSettings(novo);
      setMessages([]);
    } catch {
      /* ignora */
    }
  }

  const vazio = messages.length === 0;

  return (
    <div className="app">
      <SettingsPanel
        settings={settings}
        dbs={dbs}
        onToggleHitl={onToggleHitl}
        onToggleRag={onToggleRag}
        onChangeDb={onChangeDb}
        onNewChat={onNewChat}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="chat">
        <header className="chat-header">
          {!sidebarOpen && (
            <button className="icon-btn" onClick={() => setSidebarOpen(true)} title="Menu">
              ☰
            </button>
          )}
          <span className="chat-title">Converse com seu banco de dados</span>
          <span className="db-chip">{settings.db_name}</span>
        </header>

        <div className="messages">
          {vazio && (
            <div className="welcome">
              <h1>Olá! 👋</h1>
              <p>
                Faça uma pergunta em português sobre o seu banco de dados. Eu gero a
                consulta, executo e te mostro a resposta, com tabela e gráfico quando fizer
                sentido.
              </p>
              <div className="examples">
                {EXEMPLOS.map((ex) => (
                  <button key={ex} className="chip" onClick={() => handleSend(ex)}>
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <ChatMessage key={i} msg={m} />
          ))}

          {loading && (
            <div className="msg msg-assistant">
              <div className="avatar">T2I</div>
              <div className="bubble bubble-assistant typing">
                <span></span>
                <span></span>
                <span></span>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <MessageInput onSend={handleSend} loading={loading} awaiting={settings.awaiting} />
      </main>
    </div>
  );
}

function basename(p) {
  return p ? p.split(/[\\/]/).pop() : p;
}
