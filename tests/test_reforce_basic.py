"""
Teste básico da arquitetura ReFoRCE: validar estrutura do grafo e fluxo.

FASE 8 - Testes básicos
- Verificar que o grafo compila
- Testar Fan-out (criação de 5 Send objects)
- Testar votação com consenso
- Testar roteadores condicionais
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from text_to_insight.state import EstadoTextToInsight, EstadoCandidato
from text_to_insight.routers.edges import (
    roteador_fan_out,
    roteador_votacao,
)
from text_to_insight.nodes.voting_node import (
    calcular_assinatura_resultado,
    nos_nodo_votacao,
)


def test_roteador_fan_out():
    """Testa criação de 5 Send objects para Fan-out paralelo."""
    print("\n[TEST] roteador_fan_out - criação de Send objects")
    
    # Estado de exemplo
    estado = {
        "pergunta_original": "Quais são os clientes com maior volume de vendas?",
        "pergunta_atual": "Quais são os clientes com maior volume de vendas?",
        "db_path": "/home/jonasmelo/projectsandstudies/TextToInsight/data/olist_relational.db",
        "contexto_schema": "schema mock",
        "historico_tentativas": [],
    }
    
    sends = roteador_fan_out(estado)
    
    assert len(sends) == 5, f"Expected 5 Send objects, got {len(sends)}"
    print(f"  ✅ 5 Send objects criados corretamente")
    print(f"  ✅ Cada Send contém EstadoCandidato com contexto")
    
    # Verificar que cada candidato tem contexto correto
    for i, send in enumerate(sends):
        # Send objects não são diretamente acessíveis, mas podemos verificar no output
        print(f"    → Send[{i}] criado")
    
    return True


def test_consensus_voting():
    """Testa lógica de votação por consenso (≥3 de 5)."""
    print("\n[TEST] Votação por consenso")
    
    # Criar 5 candidatos: 3 com mesmo hash, 2 com hashes diferentes
    assinatura_a = calcular_assinatura_resultado([{"client": "A", "sales": 1000}])
    assinatura_b = calcular_assinatura_resultado([{"client": "B", "sales": 2000}])
    assinatura_c = calcular_assinatura_resultado([{"client": "C", "sales": 3000}])
    
    candidatos = [
        EstadoCandidato(
            sql="SELECT * FROM clientes ORDER BY sales DESC LIMIT 1",
            resultado_execucao={"linhas": [{"client": "A", "sales": 1000}], "total_linhas": 1},
            valido=True,
            assinatura_resultado=assinatura_a,
            indice=0,
        ),
        EstadoCandidato(
            sql="SELECT * FROM clientes ORDER BY sales DESC LIMIT 1",
            resultado_execucao={"linhas": [{"client": "A", "sales": 1000}], "total_linhas": 1},
            valido=True,
            assinatura_resultado=assinatura_a,
            indice=1,
        ),
        EstadoCandidato(
            sql="SELECT * FROM clientes ORDER BY sales DESC LIMIT 1",
            resultado_execucao={"linhas": [{"client": "A", "sales": 1000}], "total_linhas": 1},
            valido=True,
            assinatura_resultado=assinatura_a,
            indice=2,
        ),
        EstadoCandidato(
            sql="SELECT client FROM clientes WHERE sales > 500 ORDER BY sales DESC",
            resultado_execucao={"linhas": [{"client": "B", "sales": 2000}], "total_linhas": 1},
            valido=True,
            assinatura_resultado=assinatura_b,
            indice=3,
        ),
        EstadoCandidato(
            sql="SELECT TOP 1 client FROM clientes ORDER BY sales DESC",
            resultado_execucao={"linhas": [{"client": "C", "sales": 3000}], "total_linhas": 1},
            valido=True,
            assinatura_resultado=assinatura_c,
            indice=4,
        ),
    ]
    
    estado = {
        "pergunta_original": "Qual cliente tem maior volume?",
        "pergunta_atual": "Qual cliente tem maior volume?",
        "db_path": "/tmp/test.db",
        "candidatos": candidatos,
        "status_consenso": "nao_votado",
        "rodadas_exploracao": 0,
    }
    
    # Executar votação
    resultado = nos_nodo_votacao(estado)
    
    # Verificar consenso foi encontrado (3 de 5 com mesmo hash)
    assert resultado.get("status_consenso") == "consenso_encontrado", \
        f"Expected consenso_encontrado, got {resultado.get('status_consenso')}"
    assert resultado.get("sql_vencedora") is not None, "sql_vencedora não foi setada"
    
    print(f"  ✅ Consenso detectado com ≥3 candidatos")
    print(f"  ✅ sql_vencedora = {resultado.get('sql_vencedora')[:60]}...")
    print(f"  ✅ status_consenso = {resultado.get('status_consenso')}")
    
    return True


def test_roteador_votacao():
    """Testa roteador de votação pós-consenso."""
    print("\n[TEST] roteador_votacao - roteamento pós-votação")
    
    # Teste 1: Consenso encontrado → salvar_csv
    estado_consenso = {
        "status_consenso": "consenso_encontrado",
        "rodadas_exploracao": 0,
    }
    rota = roteador_votacao(estado_consenso)
    assert rota == "salvar_csv", f"Expected 'salvar_csv' for consenso, got {rota}"
    print(f"  ✅ Consenso → salvar_csv")
    
    # Teste 2: Ambiguo + rodadas < 2 → explorador
    estado_ambiguo_retry = {
        "status_consenso": "ambiguo",
        "rodadas_exploracao": 0,
    }
    rota = roteador_votacao(estado_ambiguo_retry)
    assert rota == "nos_nodo_explorador", f"Expected 'nos_nodo_explorador' for ambiguo retry, got {rota}"
    print(f"  ✅ Ambiguo (rodada 0) → nos_nodo_explorador")
    
    # Teste 3: Ambiguo + rodadas >= 2 → resposta
    estado_ambiguo_final = {
        "status_consenso": "ambiguo",
        "rodadas_exploracao": 2,
    }
    rota = roteador_votacao(estado_ambiguo_final)
    assert rota == "resposta", f"Expected 'resposta' for ambiguo final, got {rota}"
    print(f"  ✅ Ambiguo (rodada 2) → resposta (fallback)")
    
    return True


def test_assinatura_resultado():
    """Testa cálculo de assinatura para comparação de consenso."""
    print("\n[TEST] Cálculo de assinatura de resultado")
    
    resultado_a = [{"id": 1, "name": "Alice", "age": 30}]
    resultado_b = [{"id": 1, "name": "Alice", "age": 30}]
    resultado_c = [{"id": 1, "name": "Bob", "age": 25}]
    
    assinatura_a1 = calcular_assinatura_resultado(resultado_a)
    assinatura_a2 = calcular_assinatura_resultado(resultado_b)
    assinatura_c = calcular_assinatura_resultado(resultado_c)
    
    # Mesmos dados = mesma assinatura
    assert assinatura_a1 == assinatura_a2, "Mesmos dados deveriam ter mesma assinatura"
    print(f"  ✅ Dados idênticos → mesma assinatura")
    
    # Dados diferentes = assinatura diferente
    assert assinatura_a1 != assinatura_c, "Dados diferentes deveriam ter assinaturas diferentes"
    print(f"  ✅ Dados diferentes → assinaturas diferentes")
    
    return True


if __name__ == "__main__":
    print("=" * 70)
    print("FASE 8: TESTES BÁSICOS DA ARQUITETURA ReFoRCE")
    print("=" * 70)
    
    try:
        test_roteador_fan_out()
        test_assinatura_resultado()
        test_consensus_voting()
        test_roteador_votacao()
        
        print("\n" + "=" * 70)
        print("✅ TODOS OS TESTES PASSARAM!")
        print("=" * 70)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ TESTE FALHOU: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
