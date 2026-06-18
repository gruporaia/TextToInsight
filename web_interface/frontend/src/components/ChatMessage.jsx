// Bolha de mensagem. Mensagens do usuario sao texto simples. Mensagens do
// assistente sao renderizadas a partir do dict normalizado do io_adapter
// (status, answer, data, chart, csv, message) -- espelha cli_interface/render.py.

import DataTable from "./DataTable.jsx";
import { csvUrl, chartUrl } from "../api.js";

export default function ChatMessage({ msg }) {
  if (msg.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="bubble bubble-user">{msg.text}</div>
      </div>
    );
  }

  // role === "assistant"
  return (
    <div className="msg msg-assistant">
      <div className="avatar">T2I</div>
      <div className="bubble bubble-assistant">{renderAssistant(msg)}</div>
    </div>
  );
}

function renderAssistant(msg) {
  // Mensagens locais simples (ex.: aviso de configuracao aplicada).
  if (msg.kind === "note") {
    return <p className="note">{msg.text}</p>;
  }

  const saida = msg.saida || {};
  const status = saida.status;

  if (status === "error") {
    return (
      <div className="callout callout-error">
        <strong>Algo deu errado.</strong>
        <p>{saida.message || "Erro durante a execucao."}</p>
      </div>
    );
  }
  if (status === "blocked") {
    return (
      <div className="callout callout-warn">
        <strong>Fluxo bloqueado.</strong>
        <p>{saida.message || "Habilite o modo de esclarecimento e tente de novo."}</p>
      </div>
    );
  }
  if (status === "awaiting") {
    return (
      <div className="callout callout-ask">
        <strong>Preciso de um esclarecimento</strong>
        <p>{saida.message || "Pode dar mais detalhes?"}</p>
        <span className="hint">Responda na caixa de mensagem abaixo.</span>
      </div>
    );
  }

  // status === "success"
  const data = saida.data || {};
  const rows = data.rows || [];
  const chart = saida.chart || {};
  const csvPath = (saida.csv || {}).path;

  return (
    <>
      {saida.answer && <p className="answer">{saida.answer}</p>}
      {rows.length > 0 && <DataTable rows={rows} total={data.total} />}
      {chart.generated && chart.path && (
        <div className="chart">
          <img src={chartUrl(chart.path)} alt="Grafico gerado" />
        </div>
      )}
      {csvPath && (
        <div className="artifacts">
          <a className="download" href={csvUrl(csvPath)}>
            ⬇ Baixar CSV
          </a>
        </div>
      )}
      {!saida.answer && rows.length === 0 && !chart.generated && (
        <p className="answer">Consulta executada, mas sem resultado para mostrar.</p>
      )}
    </>
  );
}
