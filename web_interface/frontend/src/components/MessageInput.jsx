// Caixa de envio: textarea que cresce, Enter envia e Shift+Enter quebra linha.
// Desabilita enquanto o backend processa (loading), mostrando "pensando...".

import { useRef } from "react";

export default function MessageInput({ onSend, loading, awaiting }) {
  const ref = useRef(null);

  function submit() {
    const el = ref.current;
    const texto = el.value.trim();
    if (!texto || loading) return;
    onSend(texto);
    el.value = "";
    el.style.height = "auto";
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function autoGrow(e) {
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }

  const placeholder = awaiting
    ? "Responda o esclarecimento pedido acima..."
    : "Pergunte algo sobre o seu banco de dados...";

  return (
    <div className="composer">
      <textarea
        ref={ref}
        rows={1}
        placeholder={placeholder}
        onKeyDown={onKeyDown}
        onInput={autoGrow}
        disabled={loading}
      />
      <button onClick={submit} disabled={loading} className="send-btn">
        {loading ? "pensando…" : "Enviar"}
      </button>
    </div>
  );
}
