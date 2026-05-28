#!/usr/bin/env python3
"""
Setup Spider2-Lite: Converte JSONs em bancos SQLite.

O Spider2-Lite fornece dados em JSONs por tabela dentro de cada pasta de banco.
Este script cria arquivos .sqlite correspondentes.

Uso:
    python scripts/setup_spider2_sqlite.py
"""

import json
import sqlite3
from pathlib import Path


def create_sqlite_from_jsons(db_dir: Path, output_path: Path) -> bool:
    """Cria um arquivo SQLite importando JSONs como tabelas."""
    if not db_dir.exists():
        return False
    
    json_files = list(db_dir.glob("*.json"))
    if not json_files:
        return False
    
    try:
        conn = sqlite3.connect(str(output_path))
        cursor = conn.cursor()
        
        for json_file in sorted(json_files):
            table_name = json_file.stem.lower()
            
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                if isinstance(data, dict) and len(data) > 0:
                    records = list(data.values())
                elif isinstance(data, list):
                    records = data
                else:
                    continue
                
                if not records:
                    continue
                
                first_record = records[0] if isinstance(records[0], dict) else {}
                columns = list(first_record.keys()) if first_record else []
                
                if not columns:
                    continue
                
                # Criar tabela
                col_defs = ", ".join(f'"{col}" TEXT' for col in columns)
                create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({col_defs})'
                cursor.execute(create_sql)
                
                # Inserir dados
                placeholders = ", ".join(["?"] * len(columns))
                col_names = ", ".join(f'"{col}"' for col in columns)
                insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                
                for record in records:
                    if isinstance(record, dict):
                        values = [record.get(col, None) for col in columns]
                        cursor.execute(insert_sql, values)
                
                conn.commit()
                
            except Exception as e:
                pass
        
        conn.close()
        return True
        
    except Exception as e:
        return False


def main():
    data_dir = Path("data/spider2-lite")
    sqlite_source_dir = data_dir / "resource" / "databases" / "sqlite"
    
    if not sqlite_source_dir.exists():
        print(f"❌ Diretório não existe: {sqlite_source_dir}")
        return

    print("🔄 Convertendo JSONs Spider2-Lite em SQLite...")
    print(f"Fonte: {sqlite_source_dir}")

    db_dirs = sorted([d for d in sqlite_source_dir.iterdir() if d.is_dir()])

    success = 0
    for db_dir in db_dirs:
        db_name = db_dir.name
        output_path = db_dir / f"{db_name}.sqlite"
        
        if output_path.exists():
            success += 1
            continue
        
        if create_sqlite_from_jsons(db_dir, output_path):
            print(f"✓ {db_name}.sqlite criado")
            success += 1

    print(f"\n✅ Concluído: {success}/{len(db_dirs)} bancos processados")


if __name__ == "__main__":
    main()
