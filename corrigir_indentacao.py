#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Corretor de Indentação
======================

Corrige o erro de indentação na linha 2386
"""

import sys
from pathlib import Path

def corrigir_indentacao(arquivo_path: str):
    """Corrige a indentação do código inserido"""
    
    path = Path(arquivo_path)
    
    if not path.exists():
        print(f"❌ Arquivo não encontrado: {arquivo_path}")
        return False
    
    # Lê arquivo
    print(f"📖 Lendo arquivo: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Procura a linha com erro
    linha_erro = None
    for i, line in enumerate(lines):
        if 'if not matches and texto_corrigido:' in line:
            linha_erro = i
            break
    
    if linha_erro is None:
        print("❌ Não encontrou a linha com erro")
        return False
    
    print(f"✅ Encontrou linha com erro: {linha_erro + 1}")
    
    # Verifica a linha anterior
    linha_anterior = lines[linha_erro - 1].rstrip()
    print(f"📝 Linha anterior: {repr(linha_anterior)}")
    
    # Se a linha anterior termina com ':', precisa ter bloco indentado
    if linha_anterior.endswith(':'):
        print("⚠️  Linha anterior é um 'if' que precisa de bloco")
        
        # Adiciona 'pass' na linha anterior
        # Encontra a indentação da linha anterior
        indent_anterior = len(linha_anterior) - len(linha_anterior.lstrip())
        indent_bloco = ' ' * (indent_anterior + 4)
        
        # Remove a linha anterior problemática se for só "if not matches:"
        if 'if not matches:' in linha_anterior and 'and' not in linha_anterior:
            print("🔧 Removendo linha 'if not matches:' incompleta")
            lines.pop(linha_erro - 1)
            linha_erro -= 1
        else:
            # Adiciona pass
            print(f"➕ Adicionando 'pass' com indentação {indent_bloco}")
            lines.insert(linha_erro, f"{indent_bloco}pass\n")
            linha_erro += 1
    
    # Verifica indentação da linha com erro
    linha_atual = lines[linha_erro]
    indent_atual = len(linha_atual) - len(linha_atual.lstrip())
    
    print(f"📏 Indentação atual: {indent_atual} espaços")
    
    # A linha deve ter indentação de 8 espaços (dentro de função de classe)
    if indent_atual != 8:
        print(f"🔧 Corrigindo indentação para 8 espaços")
        conteudo = linha_atual.lstrip()
        lines[linha_erro] = ' ' * 8 + conteudo
    
    # Verifica as próximas linhas também
    print("🔍 Verificando próximas linhas...")
    for i in range(linha_erro + 1, min(linha_erro + 10, len(lines))):
        if lines[i].strip() and not lines[i].strip().startswith('#'):
            # Linha com conteúdo
            indent = len(lines[i]) - len(lines[i].lstrip())
            
            # Se é continuação do bloco 'if', deve ter 12 espaços
            if 'return' in lines[i] or 'partes_divididas' in lines[i]:
                if indent != 12:
                    print(f"  Linha {i+1}: corrigindo para 12 espaços")
                    conteudo = lines[i].lstrip()
                    lines[i] = ' ' * 12 + conteudo
    
    # Salva
    print("💾 Salvando correções...")
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print("\n" + "="*60)
    print("✅ INDENTAÇÃO CORRIGIDA!")
    print("="*60)
    print("\n🧪 Teste novamente:")
    print(f"   python {path.name} --lei codigo-civil --planalto-html CCNEWOFICIAL.htm -o teste.json")
    
    return True

if __name__ == '__main__':
    print("="*60)
    print("CORRETOR DE INDENTAÇÃO")
    print("="*60)
    print()
    
    arquivo = 'importer_normas_leg.py'
    
    if not Path(arquivo).exists():
        print(f"❌ {arquivo} não encontrado")
        print(f"📂 Diretório atual: {Path.cwd()}")
        sys.exit(1)
    
    if corrigir_indentacao(arquivo):
        sys.exit(0)
    else:
        sys.exit(1)
