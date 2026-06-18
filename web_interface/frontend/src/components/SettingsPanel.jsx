// Painel lateral de configuracoes, em linguagem amigavel (sem jargao de dev).
// Equivale aos slash commands do CLU (/hitl, /rag, /db, /new), mas como controles
// graficos. Cada mudanca chama o backend, que reconstroi o engine na proxima
// pergunta.

export default function SettingsPanel({
  settings,
  dbs,
  onToggleHitl,
  onToggleRag,
  onChangeDb,
  onNewChat,
  open,
  onClose,
}) {
  return (
    <aside className={`sidebar ${open ? "open" : ""}`}>
      <div className="sidebar-head">
        <h2>Text-to-Insight</h2>
        <button className="icon-btn close-btn" onClick={onClose} title="Fechar">
          ✕
        </button>
      </div>

      <button className="new-chat-btn" onClick={onNewChat}>
        ＋ Nova conversa
      </button>

      <div className="setting-group">
        <label className="setting-label">Banco de dados</label>
        <select
          value={settings.db_path || ""}
          onChange={(e) => onChangeDb(e.target.value)}
        >
          {dbs.length === 0 && <option value="">{settings.db_name || "—"}</option>}
          {dbs.map((db) => (
            <option key={db} value={db}>
              {basename(db)}
            </option>
          ))}
        </select>
        <p className="setting-hint">O arquivo .db que você quer consultar.</p>
      </div>

      <div className="setting-group">
        <label className="switch-row">
          <span>
            Pedir esclarecimento
            <span className="setting-hint">
              Quando a pergunta for ambígua, o assistente pergunta antes de responder.
            </span>
          </span>
          <Toggle checked={!!settings.hitl} onChange={onToggleHitl} />
        </label>
      </div>

      <div className="setting-group">
        <label className="switch-row">
          <span>
            Usar conhecimento extra
            <span className="setting-hint">
              Dá ao assistente mais contexto sobre a estrutura do banco (RAG).
            </span>
          </span>
          <Toggle checked={!!settings.rag} onChange={onToggleRag} />
        </label>
      </div>

      <div className="sidebar-foot">
        <p>Conversa: {settings.thread_id}</p>
      </div>
    </aside>
  );
}

function Toggle({ checked, onChange }) {
  return (
    <button
      className={`toggle ${checked ? "on" : ""}`}
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
    >
      <span className="knob" />
    </button>
  );
}

function basename(p) {
  return p ? p.split(/[\\/]/).pop() : p;
}
