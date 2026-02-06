#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CORREÇÃO MELHORADA v2.0
=======================

Esta versão resolve o bug SEM quebrar todo o parsing.

PROBLEMA ORIGINAL:
- HTML: "<p>CAPÍTULO II<br/>Do Título ao Portador</p>"
- Parser juntava: "CAPÍTULO II Do Título ao Portador" (1 bloco)
- Resultado: path['capitulo'] = "CAPÍTULO II Do Título ao Portador" ✓
- MAS: Lógica depois quebrava, criando títulos fantasmas

SOLUÇÃO v2:
- NÃO dividir blocos na extração
- Dividir APENAS na hora de processar estrutura
- Preservar comportamento original para artigos e outros elementos
"""

import sys
from pathlib import Path
import shutil
import re

def criar_correcao_v2():
    """Retorna o código da correção v2"""
    
    # Esta correção vai DENTRO da função _atualizar_estrutura
    # ao invés de modificar _html_para_blocos
    
    correcao = '''
    def _processar_rotulo_com_br(self, bloco_original: str) -> list:
        """
        Divide rotulos que contém quebras implícitas.
        
        Exemplo: "CAPÍTULO II Do Título ao Portador"
        Retorna: ["CAPÍTULO II", "Do Título ao Portador"]
        
        Isso permite processar separadamente:
        - Bloco 1: Detecta estrutura "CAPÍTULO II"
        - Bloco 2: Consome como descrição
        """
        # Padrões de estrutura com número
        patterns = [
            (r'(CAP[ÍI][TL]ULO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'CAPITULO'),
            (r'(T[ÍI]TULO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'TITULO'),
            (r'(SE[CÇ]ÃO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'SECAO'),
            (r'(SUBSE[CÇ]ÃO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'SUBSECAO'),
            (r'(LIVRO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'LIVRO'),
        ]
        
        upper = bloco_original.upper()
        
        for pattern, tipo in patterns:
            match = re.search(pattern, upper)
            if match:
                # Encontrou padrão "ESTRUTURA NUM + TEXTO"
                # Verifica se o texto começa com preposição (Da, Do, Das, Dos, De)
                resto = bloco_original[match.end(1):]
                resto_limpo = resto.strip()
                
                if resto_limpo and len(resto_limpo) > 2:
                    # Tem texto depois
                    primeira_palavra = resto_limpo.split()[0] if resto_limpo.split() else ''
                    
                    # Se começa com preposição, é descrição
                    if primeira_palavra.upper() in ['DA', 'DO', 'DAS', 'DOS', 'DE', 'D']:
                        # Divide em duas partes
                        parte1 = bloco_original[:match.end(1)].strip()
                        parte2 = resto_limpo
                        return [parte1, parte2]
        
        # Não encontrou padrão - retorna original
        return [bloco_original]
'''
    
    return correcao

def aplicar_correcao_v2(arquivo_path: str):
    """Aplica correção v2"""
    
    path = Path(arquivo_path)
    
    if not path.exists():
        print(f"❌ Arquivo não encontrado: {arquivo_path}")
        return False
    
    # Backup
    backup_path = path.with_suffix('.py.backup_v2')
    print(f"📦 Criando backup: {backup_path}")
    shutil.copy2(path, backup_path)
    
    # Lê arquivo
    print(f"📖 Lendo arquivo...")
    with open(path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Remove correção antiga se existir
    print("🧹 Limpando correções antigas...")
    lines_limpas = []
    dentro_correcao = False
    
    for line in lines:
        if 'CORREÇÃO:' in line or 'CORREÇÃO MELHORADA:' in line:
            dentro_correcao = True
        elif dentro_correcao and 'continue' in line:
            dentro_correcao = False
            continue
        
        if not dentro_correcao:
            lines_limpas.append(line)
    
    lines = lines_limpas
    
    # Encontra onde adicionar a nova função
    # Procura pela classe ParserTextoNormas
    linha_classe = None
    for i, line in enumerate(lines):
        if 'class ParserTextoNormas:' in line:
            linha_classe = i
            break
    
    if linha_classe is None:
        print("❌ Não encontrou classe ParserTextoNormas")
        return False
    
    # Adiciona a nova função após __init__
    linha_init = None
    for i in range(linha_classe, min(linha_classe + 100, len(lines))):
        if 'def __init__' in lines[i]:
            # Encontra o fim do __init__ (próxima linha que não é indentada ou próximo def)
            for j in range(i + 1, min(i + 200, len(lines))):
                if lines[j].strip() and not lines[j].startswith('        ') and not lines[j].startswith('\t\t'):
                    linha_init = j
                    break
            break
    
    if linha_init is None:
        print("❌ Não encontrou local para inserir função")
        return False
    
    print(f"✅ Inserindo nova função na linha {linha_init}")
    
    # Insere nova função
    nova_funcao = '''
    def _processar_rotulo_com_br(self, bloco_original: str) -> list:
        """
        Divide rotulos que contém quebras implícitas.
        
        Exemplo: "CAPÍTULO II Do Título ao Portador"
        Retorna: ["CAPÍTULO II", "Do Título ao Portador"]
        """
        patterns = [
            (r'(CAP[ÍI][TL]ULO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'CAPITULO'),
            (r'(T[ÍI]TULO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'TITULO'),
            (r'(SE[CÇ]ÃO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'SECAO'),
            (r'(SUBSE[CÇ]ÃO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'SUBSECAO'),
            (r'(LIVRO\\s+[IVXLCDM0-9-]+)\\s+([A-Z])', 'LIVRO'),
        ]
        
        upper = bloco_original.upper()
        
        for pattern, tipo in patterns:
            match = re.search(pattern, upper)
            if match:
                resto = bloco_original[match.end(1):]
                resto_limpo = resto.strip()
                
                if resto_limpo and len(resto_limpo) > 2:
                    primeira_palavra = resto_limpo.split()[0] if resto_limpo.split() else ''
                    
                    if primeira_palavra.upper() in ['DA', 'DO', 'DAS', 'DOS', 'DE', 'D']:
                        parte1 = bloco_original[:match.end(1)].strip()
                        parte2 = resto_limpo
                        return [parte1, parte2]
        
        return [bloco_original]

'''
    
    lines.insert(linha_init, nova_funcao)
    
    # Agora modifica _segmentar_rotulos_multinivel para usar a nova função
    print("🔧 Modificando _segmentar_rotulos_multinivel...")
    
    for i, line in enumerate(lines):
        if 'def _segmentar_rotulos_multinivel' in line:
            # Encontra o corpo da função
            # Procura por "return [texto_corrigido]" ou "return segmentos"
            for j in range(i, min(i + 50, len(lines))):
                if 'return [texto_corrigido]' in lines[j] or 'return segmentos or [texto_corrigido]' in lines[j]:
                    # Antes do return, adiciona processamento adicional
                    indent = ' ' * 8  # Indentação padrão
                    novo_codigo = f'''{indent}# CORREÇÃO v2: Divide blocos com estrutura + descrição junto
{indent}if not matches and texto_corrigido:
{indent}    # Não tem múltiplos níveis, mas pode ter estrutura + descrição
{indent}    partes_divididas = self._processar_rotulo_com_br(texto_corrigido)
{indent}    if len(partes_divididas) > 1:
{indent}        return partes_divididas
{indent}
'''
                    lines.insert(j, novo_codigo)
                    print(f"✅ Código inserido na linha {j}")
                    break
            break
    
    # Salva
    print("💾 Salvando arquivo...")
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    
    print("\n" + "="*70)
    print("✅ CORREÇÃO V2 APLICADA COM SUCESSO!")
    print("="*70)
    print("\n📝 Mudanças:")
    print("  1. Nova função: _processar_rotulo_com_br()")
    print("  2. Modificada: _segmentar_rotulos_multinivel()")
    print("\n💡 Como funciona:")
    print("  - Detecta padrões: 'CAPÍTULO II Do Título...'")
    print("  - Divide em: ['CAPÍTULO II', 'Do Título...']")
    print("  - Parser processa cada parte separadamente")
    print("\n🧪 Teste:")
    print(f"  python {path.name} --planalto-html CCNEWOFICIAL.htm -o teste.json")
    
    return True

if __name__ == '__main__':
    print("="*70)
    print(" CORREÇÃO V2 - Solução Cirúrgica (Não Quebra Parsing)")
    print("="*70)
    print()
    
    arquivo = 'importer_normas_leg.py'
    
    if not Path(arquivo).exists():
        print(f"❌ {arquivo} não encontrado")
        print(f"📂 Diretório atual: {Path.cwd()}")
        print("\n💡 Execute este script no mesmo diretório do importer_normas_leg.py")
        sys.exit(1)
    
    if aplicar_correcao_v2(arquivo):
        print("\n" + "="*70)
        print("✅ TUDO PRONTO!")
        print("="*70)
        sys.exit(0)
    else:
        print("\n❌ Falha ao aplicar correção")
        sys.exit(1)
