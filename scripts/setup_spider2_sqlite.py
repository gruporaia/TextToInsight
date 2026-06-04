#!/usr/bin/env python3
import sqlite3
from pathlib import Path
import pandas as pd

def create_sqlite_from_jsons(db_dir: Path, output_path: Path) -> bool:
    if not db_dir.exists():
        return False
    
    json_files = list(db_dir.glob("*.json"))
    if not json_files:
        return False
    
    try:
        # Se o banco já existir, removemos para não dar conflito e criar do zero
        if output_path.exists():
            output_path.unlink()
            
        conn = sqlite3.connect(str(output_path))
        
        for json_file in sorted(json_files):
            table_name = json_file.stem.lower()
            
            try:
                # O Pandas lida maravilhosamente bem com a conversão de JSON para SQL
                df = pd.read_json(json_file)
                # Salva direto no banco
                df.to_sql(table_name, conn, if_exists='replace', index=False)
            except Exception as e:
                # Se o formato for linha a linha (JSON Lines)
                try:
                    df = pd.read_json(json_file, lines=True)
                    df.to_sql(table_name, conn, if_exists='replace', index=False)
                except Exception as e2:
                    print(f"    ❌ Erro real na tabela {table_name}: {e2}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Erro fatal ao criar banco {db_dir.name}: {e}")
        return False


def main():
    data_dir = Path("data/spider2-lite")
    sqlite_source_dir = data_dir / "resource" / "databases" / "sqlite"
    
    if not sqlite_source_dir.exists():
        print(f"❌ Diretório não existe: {sqlite_source_dir}")
        return

    print("🔄 Convertendo JSONs Spider2-Lite em SQLite usando Pandas...")
    
    db_dirs = sorted([d for d in sqlite_source_dir.iterdir() if d.is_dir()])
    success = 0
    
    for db_dir in db_dirs:
        db_name = db_dir.name
        output_path = db_dir / f"{db_name}.sqlite"
        
        if create_sqlite_from_jsons(db_dir, output_path):
            print(f"✓ {db_name}.sqlite criado e populado!")
            success += 1

    print(f"\n✅ Concluído: {success}/{len(db_dirs)} bancos processados com tabelas injetadas.")


if __name__ == "__main__":
    main()