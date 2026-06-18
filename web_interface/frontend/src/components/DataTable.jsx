// Tabela dos dados retornados (data.rows). Trunca em MAX_LINHAS linhas, igual ao
// MAX_LINHAS_TABELA do CLI (cli_interface/render.py): o conjunto completo vai no CSV.

const MAX_LINHAS = 20;

export default function DataTable({ rows, total }) {
  if (!rows || rows.length === 0) return null;

  const primeira = rows[0];
  const colunas =
    primeira && typeof primeira === "object" && !Array.isArray(primeira)
      ? Object.keys(primeira)
      : null;

  const visiveis = rows.slice(0, MAX_LINHAS);
  const totalNum = Number(total) || rows.length;

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        {colunas && (
          <thead>
            <tr>
              {colunas.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {visiveis.map((r, i) => (
            <tr key={i}>
              {colunas
                ? colunas.map((c) => <td key={c}>{fmt(r[c])}</td>)
                : asCells(r).map((v, j) => <td key={j}>{fmt(v)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {totalNum > visiveis.length && (
        <p className="data-table-footer">
          mostrando {visiveis.length} de {totalNum} linhas (conjunto completo no CSV).
        </p>
      )}
    </div>
  );
}

function asCells(r) {
  return Array.isArray(r) ? r : [r];
}

function fmt(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
