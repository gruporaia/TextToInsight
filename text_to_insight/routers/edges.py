"""
Funções de roteamento condicional para o grafo Text-to-Insight.

As arestas condicionais decidem para qual nó o grafo deve prosseguir
baseado nas condições do estado atual.
"""

from typing import Literal
from ..state import EstadoTextToInsight


def roteador_sandbox(estado: EstadoTextToInsight) -> Literal["critico", "planejador"]:
    """
    Roteador após execução do Executor (sandbox).

    - exec_ok → critico (avaliar resultado)
    - exec_erro + tentativas < 3 → planejador (reconsiderar)
    - tentativas >= 3 → planejador (desistir/reiniciar)
    """
    status = estado.get("status", "")
    tentativas = estado.get("tentativas_loop", 0)

    print(f"[ROTEADOR_SANDBOX] Status: {status}, Tentativas: {tentativas}")

    if status == "exec_ok":
        print("[ROTEADOR_SANDBOX] Execução OK → critico")
        return "critico"

    if status == "exec_erro" and tentativas < 3:
        print("[ROTEADOR_SANDBOX] Erro detectado → planejador para retry")
        return "planejador"

    print("[ROTEADOR_SANDBOX] Muitas tentativas ou erro → planejador")
    return "planejador"


def roteador_planejador(estado: EstadoTextToInsight) -> Literal["esquema", "agente_codigo", "critico", "fim"]:
    """
    Roteador após Planejador.

    - contexto_schema vazio → esquema
    - pronto_codificacao ou revisando_estrategia → agente_codigo
    - aprovado → fim
    """
    contexto = estado.get("contexto_schema", "")
    status = estado.get("status", "")
    esperar = estado.get("espera_humana", False)

    print(f"[ROTEADOR_PLANEJADOR] Status: {status}, Schema preenchido: {bool(contexto)}")

    if esperar:
        print("[ROTEADOR_PLANEJADOR] Espera humana ativa → espera_humana")
        return "espera_humana"

    if not contexto:
        print("[ROTEADOR_PLANEJADOR] Schema vazio → esquema")
        return "esquema"

    if status in ("pronto_codificacao", "revisando_estrategia"):
        print("[ROTEADOR_PLANEJADOR] → agente_codigo")
        return "agente_codigo"

    if status == "aprovado":
        print("[ROTEADOR_PLANEJADOR] Aprovado → fim")
        return "fim"

    # Default: gera código
    print("[ROTEADOR_PLANEJADOR] Default → planejador")
    return "planejador"
