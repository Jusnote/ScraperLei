#!/usr/bin/env python3
"""
Script para importar leis do JSON local para o Supabase.
Execute: python import_to_supabase.py

Requisitos:
  pip install supabase python-dotenv
"""

import json
import os
import sys
from pathlib import Path
from supabase import create_client, Client

# Configuração Supabase
SUPABASE_URL = "https://xmtleqquivcukwgdexhc.supabase.co"
# Service role key (JWT format) - permite bypass de RLS
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhtdGxlcXF1aXZjdWt3Z2RleGhjIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1MzIyOTk5NywiZXhwIjoyMDY4ODA1OTk3fQ.a-n50z1KmRa7JRoREBJKf3kSoXfU9U-t8G_PXnBFqDI"

# Diretório dos JSONs
LEIS_DIR = Path(__file__).parent


def get_ordem_numerica(numero: str) -> float:
    """
    Calcula ordem numérica considerando sufixos (ex: 121-A > 121).
    Formato: Numero . (ValorAsciiSufixo / 1000)
    Ex: 
      "121"   -> 121.0
      "121-A" -> 121.001
      "121-B" -> 121.002
    """
    import re
    # Remove pontos de milhar apenas para parsing
    clean_num = numero.replace('.', '')
    
    match = re.match(r'^(\d+)(.*)$', clean_num)
    if not match:
        return 0.0
        
    base = float(match.group(1))
    suffix = match.group(2).strip().upper().replace('-', '').replace('º', '')
    
    if not suffix:
        return base
        
    # Heurística simples para sufixos: A=1, B=2... AA=27...
    # Assumindo sufixos simples (letra única) para maioria dos casos
    suffix_val = 0
    if len(suffix) == 1 and suffix.isalpha():
        suffix_val = ord(suffix) - ord('A') + 1
    elif suffix.isdigit():
        # Caso raro de sub-numero "121-1"? Se existir, trata como decimal
        suffix_val = int(suffix)
    else:
        # Fallback para outros casos
        suffix_val = 999 
        
    return base + (suffix_val / 1000.0)

def get_supabase_client() -> Client:
    """Cria cliente Supabase com service_role key"""
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def load_index() -> list:
    """Carrega o índice de leis disponíveis"""
    index_path = LEIS_DIR / "index.json"
    with open(index_path, 'r', encoding='utf-8') as f:
        return json.load(f).get('leis', [])


def load_lei(lei_id: str) -> dict:
    """Carrega uma lei específica do JSON"""
    lei_path = LEIS_DIR / f"{lei_id}.json"
    with open(lei_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def import_lei(supabase: Client, lei_data: dict, index_entry: dict):
    """Importa uma lei e seus artigos para o Supabase"""
    lei = lei_data.get('lei', {})
    artigos = lei_data.get('artigos', [])

    lei_id = lei.get('id') or index_entry.get('id')

    print(f"\n📋 Importando: {index_entry.get('nome', lei_id)}")
    print(f"   ID: {lei_id}")
    print(f"   Artigos: {len(artigos)}")

    # 1. Inserir/Atualizar lei
    lei_record = {
        'id': lei_id,
        'numero': index_entry.get('numero', '').strip(),
        'nome': index_entry.get('nome', '').strip(),
        'sigla': index_entry.get('sigla', '').strip(),
        'ementa': index_entry.get('ementa', '').strip(),
        'data_publicacao': index_entry.get('data') if index_entry.get('data') else None,
        'hierarquia': lei.get('hierarquia', {}),
        'total_artigos': len(artigos)
    }

    # Upsert lei
    result = supabase.table('leis').upsert(lei_record).execute()
    print(f"   ✅ Lei inserida/atualizada")

    # 2. Inserir artigos em lotes
    if not artigos:
        print(f"   ⚠️  Nenhum artigo encontrado")
        return

    # Identificar artigos revogados que jÃ¡ estÃ£o "absorvidos" por uma versÃ£o vigente
    absorbed_ids = set()
    for artigo in artigos:
        if not artigo.get('vigente'):
            continue
        for revoked in artigo.get('revoked_versions', []) or []:
            revoked_id = revoked.get('id')
            if revoked_id:
                absorbed_ids.add(revoked_id)

    # Preparar registros de artigos
    artigos_records = []
    for artigo in artigos:
        # Se for um dispositivo revogado jÃ¡ presente em revoked_versions de um vigente, ignora
        if artigo.get('id') in absorbed_ids:
            continue
        artigos_records.append({
            'id': artigo.get('id'),
            'lei_id': lei_id,
            'numero': artigo.get('numero', ''),
            'slug': artigo.get('slug', ''),
            'plate_content': artigo.get('plate_content'),
            'texto_plano': artigo.get('texto_plano', ''),
            'search_text': artigo.get('search_text', ''),
            'vigente': artigo.get('vigente', True),
            'contexto': artigo.get('contexto', ''),
            'path': artigo.get('path', {}),
            'content_hash': artigo.get('content_hash'),
            # Calcula ordem precisa para evitar colisão (121 vs 121-A)
            'ordem_numerica': get_ordem_numerica(artigo.get('numero', '')),
            'epigrafe': artigo.get('epigrafe', ''),
            'revoked_versions': artigo.get('revoked_versions', [])
        })
        if artigo.get('numero') == '121' or artigo.get('numero') == '1':
             print(f"[DEBUG PAYLOAD] {artigo.get('numero')} Epigrafe: '{artigo.get('epigrafe', '')}'")

    # Inserir em lotes de 100 (limite do Supabase)
    # Inserir em lotes de 100 (limite do Supabase)
    BATCH_SIZE = 100
    
    # Busca hashes existentes para diff
    print(f"   🔍 Verificando mudanças (Diff)...")
    try:
        existing_hashes = {}
        # Seleciona apenas id e content_hash para economizar banda
        response = supabase.table('artigos').select("id, content_hash").eq("lei_id", lei_id).execute()
        if response.data:
            for item in response.data:
                existing_hashes[item['id']] = item.get('content_hash')
    except Exception as e:
        print(f"   ⚠️  Não foi possível buscar hashes existentes (Diff desativado): {e}")
        existing_hashes = {}

    to_upsert = []
    seen_ids = set()
    skipped_count = 0
    
    for record in artigos_records:
        art_id = record['id']
        new_hash = record.get('content_hash')
        
        # Deduplicação de segurança: Se ID já está na lista de envio, ignora
        if art_id in seen_ids:
            continue
            
        # FORCE UPDATE for Ordem Numerica Fix
        # if art_id in existing_hashes and existing_hashes[art_id] == new_hash:
        #    skipped_count += 1
        #    continue
        
        # Se não existe ou mudou, adiciona para upsert
        seen_ids.add(art_id)
        to_upsert.append(record)

    if skipped_count > 0:
        print(f"   ⏩ {skipped_count} artigos inalterados (pulados)")

    if not to_upsert:
        print(f"   ✨ Nenhuma alteração detectada!")
        return

    # Processa upserts apenas do que mudou
    total_batches = (len(to_upsert) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(to_upsert), BATCH_SIZE):
        batch = to_upsert[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1

        try:
            result = supabase.table('artigos').upsert(batch).execute()
            print(f"   📦 Lote {batch_num}/{total_batches}: {len(batch)} artigos atualizados")
        except Exception as e:
            print(f"   ❌ Erro no lote {batch_num}: {e}")
            # Tentar inserir um por um para identificar o problema
            for artigo in batch:
                try:
                    supabase.table('artigos').upsert(artigo).execute()
                except Exception as e2:
                    print(f"      ❌ Erro no artigo {artigo.get('id')}: {e2}")

    print(f"   ✅ {len(to_upsert)} artigos importados/atualizados")


def main():
    print("=" * 60)
    print("🚀 IMPORTAÇÃO DE LEIS PARA SUPABASE")
    print("=" * 60)

    # Conectar ao Supabase
    print("\n🔌 Conectando ao Supabase...")
    supabase = get_supabase_client()
    print("✅ Conectado!")

    # Carregar índice
    print("\n📂 Carregando índice de leis...")
    index = load_index()
    print(f"✅ {len(index)} leis encontradas no índice")

    # Importar cada lei
    for entry in index:
        lei_id = entry.get('id')

        try:
            lei_data = load_lei(lei_id)
            import_lei(supabase, lei_data, entry)
        except FileNotFoundError:
            print(f"\n⚠️  Arquivo não encontrado: {lei_id}.json")
        except Exception as e:
            print(f"\n❌ Erro ao importar {lei_id}: {e}")

    print("\n" + "=" * 60)
    print("✅ IMPORTAÇÃO CONCLUÍDA!")
    print("=" * 60)


if __name__ == '__main__':
    main()
