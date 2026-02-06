#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IMPORTER NORMAS.LEG.BR v1.0
===========================

Importador profissional que busca leis da API do normas.leg.br
e converte para o formato JSON usado pelo site.

Fontes:
  1. JSON estruturado (quando disponível) - /api/public/normas?urn=...&tipo_documento=maior-detalhe
  2. HTML do binário (fallback) - /api/public/binario/{uuid}/texto

Output: Mesmo formato do scraper_v2.py (compatível com frontend)

Uso:
  python importer_normas_leg.py --urn "urn:lex:br:federal:decreto.lei:1940-12-07;2848" --output codigp_v2.json
  python importer_normas_leg.py --lei "codigo-penal" --output codigp_v2.json
"""

import json
import re
import sys
import hashlib
import uuid as uuid_lib
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime

# Dependências
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("AVISO: requests não instalado. Use: pip install requests")

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("AVISO: beautifulsoup4 não instalado. Use: pip install beautifulsoup4")

try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.table import Table
    from rich.panel import Panel
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


# =============================================================================
# CONFIGURAÇÕES
# =============================================================================

API_BASE = "https://normas.leg.br/api/public"
CACHE_DIR = Path(".cache_normas")

# URNs conhecidos para leis comuns
LEIS_CONHECIDAS = {
    "codigo-penal": "urn:lex:br:federal:decreto.lei:1940-12-07;2848",
    "codigo-civil": "urn:lex:br:federal:lei:2002-01-10;10406",
    "clt": "urn:lex:br:federal:decreto.lei:1943-05-01;5452",
    "cdc": "urn:lex:br:federal:lei:1990-09-11;8078",
    "eca": "urn:lex:br:federal:lei:1990-07-13;8069",
    "ctb": "urn:lex:br:federal:lei:1997-09-23;9503",
    "constituicao": "urn:lex:br:federal:constituicao:1988-10-05;1988",
}


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class ElementoLei:
    """Representa um elemento da lei (artigo, parágrafo, inciso, etc.)"""
    tipo: str  # artigo, paragrafo, inciso, alinea, item, penalty
    numero: str
    texto: str
    epigrafe: str = ""
    urn: str = ""
    filhos: List['ElementoLei'] = field(default_factory=list)
    vigente: bool = True
    alterado_por: str = ""
    path: Dict[str, str] = field(default_factory=dict)  # Path hierárquico (parte, título, etc.)


@dataclass
class ArtigoOutput:
    """Formato final do artigo para o JSON de saída"""
    id: str
    numero: str
    slug: str
    epigrafe: str
    plate_content: List[Dict]
    texto_plano: str
    search_text: str
    vigente: bool
    contexto: str
    path: Dict[str, str]
    content_hash: str
    urn: str = ""  # Novo campo


# =============================================================================
# CONVERSOR URN -> SLUG
# =============================================================================

class ConversorURNSlug:
    """Converte URN LexML para formato de slug do site"""

    # Mapeamento URN -> Slug
    MAPA_TIPOS = {
        'art': 'artigo',
        'par': 'paragrafo',
        'inc': 'inciso',
        'ali': 'alinea',
        'ite': 'item',
        'cpt': 'caput',
        'prt': 'parte',
        'liv': 'livro',
        'tit': 'titulo',
        'cap': 'capitulo',
        'sec': 'secao',
    }

    # Regex para parsear sufixo URN (ex: art121_par2_inc1_alia)
    RE_URN_PARTE = re.compile(r'([a-z]+)(\d+[a-z]?(?:-[a-z])?)', re.IGNORECASE)

    @classmethod
    def urn_para_slug(cls, urn_sufixo: str) -> Tuple[str, bool]:
        """
        Converte sufixo URN para slug.

        Args:
            urn_sufixo: Ex: "art121_par2_inc1" ou "art121a_cpt"

        Returns:
            Tuple[slug, sucesso]: Ex: ("artigo-121.paragrafo-2.inciso-1", True)
        """
        if not urn_sufixo:
            return "", False

        # Remove prefixo se existir (ex: "!art121" -> "art121")
        if urn_sufixo.startswith('!'):
            urn_sufixo = urn_sufixo[1:]

        partes = urn_sufixo.split('_')
        slug_partes = []

        for parte in partes:
            # Tenta fazer match do padrão tipo+numero
            match = cls.RE_URN_PARTE.match(parte)
            if match:
                tipo_urn = match.group(1).lower()
                numero = match.group(2).lower()

                # Converte tipo
                tipo_slug = cls.MAPA_TIPOS.get(tipo_urn)
                if tipo_slug:
                    # Trata caput especialmente (não tem número no slug)
                    if tipo_urn == 'cpt':
                        slug_partes.append('caput')
                    else:
                        slug_partes.append(f"{tipo_slug}-{numero}")
                else:
                    # Tipo desconhecido - mantém como está
                    slug_partes.append(parte)
            else:
                # Não fez match - pode ser "caput" ou outro termo
                if parte.lower() == 'cpt':
                    slug_partes.append('caput')
                else:
                    slug_partes.append(parte)

        slug = '.'.join(slug_partes)

        # Validação básica
        sucesso = len(slug_partes) > 0 and slug_partes[0].startswith('artigo-')

        return slug, sucesso

    @classmethod
    def extrair_sufixo_urn(cls, urn_completo: str) -> str:
        """Extrai o sufixo do URN completo (parte após !)"""
        if '!' in urn_completo:
            return urn_completo.split('!')[-1]
        return ""

    @classmethod
    def validar_conversao(cls, urn: str, slug: str) -> bool:
        """Valida se a conversão URN->Slug está correta"""
        # Reconverte slug para URN e compara
        # Por enquanto, validação simples: verifica se tem estrutura válida

        if not slug:
            return False

        # Deve começar com artigo-X ou ser "caput"
        if not (slug.startswith('artigo-') or slug == 'caput'):
            # Pode ser parte interna (paragrafo-1.inciso-2)
            partes_validas = ['paragrafo-', 'inciso-', 'alinea-', 'item-', 'caput']
            if not any(slug.startswith(p) for p in partes_validas):
                return False

        return True


# =============================================================================
# CLIENTE API NORMAS.LEG.BR
# =============================================================================

class ClienteNormasLeg:
    """Cliente para a API do normas.leg.br"""

    def __init__(self, use_cache: bool = True):
        self.use_cache = use_cache
        if use_cache:
            CACHE_DIR.mkdir(exist_ok=True)

    def buscar_lei(self, urn: str) -> Dict[str, Any]:
        """
        Busca lei pela URN.

        Returns:
            Dict com:
              - 'tipo': 'json' ou 'html'
              - 'dados': conteúdo (dict para JSON, str para HTML)
              - 'metadados': informações da lei
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests não instalado")

        # 1. Tenta buscar JSON estruturado
        url_json = f"{API_BASE}/normas?urn={urn}&tipo_documento=maior-detalhe"

        if console:
            console.print(f"[dim]Buscando: {url_json}[/dim]")

        resp = requests.get(url_json, headers={"Accept": "application/json"}, timeout=60)

        if resp.status_code == 200:
            dados = resp.json()

            # Verifica se tem estrutura completa (hasPart)
            if 'hasPart' in dados:
                return {
                    'tipo': 'json',
                    'dados': dados,
                    'metadados': self._extrair_metadados(dados)
                }
            else:
                # Só metadados - precisa buscar HTML
                if console:
                    console.print("[yellow]JSON sem estrutura completa, buscando HTML...[/yellow]")

                # Extrai UUID do binário
                uuid_binario = self._extrair_uuid_binario(dados)
                if uuid_binario:
                    return self._buscar_html(uuid_binario, self._extrair_metadados(dados))

        raise Exception(f"Erro ao buscar lei: HTTP {resp.status_code}")

    def _extrair_uuid_binario(self, dados: Dict) -> Optional[str]:
        """
        Extrai UUID do endpoint binário dos metadados.

        Prioridade:
        1. version='Current' - versão compilada/atualizada
        2. additionalType contém 'Compilacao' ou 'Vigente'
        3. additionalType contém 'PublicacaoOriginal'
        4. Último encoding disponível (mais recente)
        """
        encodings = dados.get('encoding', [])

        def _extrair_uuid(enc: Dict) -> Optional[str]:
            """Extrai UUID da contentUrl do encoding"""
            content_url = enc.get('contentUrl', '')
            match = re.search(r'/binario/([a-f0-9-]+)/texto', content_url)
            return match.group(1) if match else None

        # Prioridade 1: version='Current' (versão compilada/atualizada)
        for enc in encodings:
            version = enc.get('version', '')
            if version == 'Current':
                uuid = _extrair_uuid(enc)
                if uuid:
                    if console:
                        console.print(f"[dim]Selecionado encoding: version=Current[/dim]")
                    return uuid

        # Prioridade 2: additionalType com Compilacao/Vigente
        for enc in encodings:
            additional_type = enc.get('additionalType', '')
            if 'Compilacao' in additional_type or 'Vigente' in additional_type:
                uuid = _extrair_uuid(enc)
                if uuid:
                    if console:
                        console.print(f"[dim]Selecionado encoding: additionalType={additional_type}[/dim]")
                    return uuid

        # Prioridade 3: PublicacaoOriginal
        for enc in encodings:
            additional_type = enc.get('additionalType', '')
            if 'PublicacaoOriginal' in additional_type:
                uuid = _extrair_uuid(enc)
                if uuid:
                    if console:
                        console.print(f"[dim]Selecionado encoding: additionalType={additional_type}[/dim]")
                    return uuid

        # Fallback: pega o ÚLTIMO disponível (geralmente mais recente)
        for enc in reversed(encodings):
            uuid = _extrair_uuid(enc)
            if uuid:
                if console:
                    console.print(f"[dim]Selecionado encoding: fallback (último disponível)[/dim]")
                return uuid

        return None

    def _buscar_html(self, uuid: str, metadados: Dict) -> Dict[str, Any]:
        """Busca HTML do endpoint binário"""
        url = f"{API_BASE}/binario/{uuid}/texto"

        if console:
            console.print(f"[dim]Buscando HTML: {url}[/dim]")

        resp = requests.get(url, timeout=60)

        if resp.status_code == 200:
            return {
                'tipo': 'html',
                'dados': resp.text,
                'metadados': metadados
            }

        raise Exception(f"Erro ao buscar HTML: HTTP {resp.status_code}")

    def _extrair_metadados(self, dados: Dict) -> Dict[str, Any]:
        """Extrai metadados básicos da lei"""
        return {
            'titulo': dados.get('headline', ''),
            'urn': dados.get('legislationIdentifier', dados.get('@id', '')),
            'data': dados.get('legislationDate', ''),
            'ementa': dados.get('abstract', ''),
            'keywords': dados.get('keywords', ''),
            'alternateName': dados.get('alternateName', []),
        }


# =============================================================================
# PARSER JSON (LEIS COM ESTRUTURA COMPLETA)
# =============================================================================

class ParserJSONNormas:
    """Parser para JSON estruturado do normas.leg.br"""

    def __init__(self, dados: Dict[str, Any]):
        self.dados = dados
        self.artigos: List[ElementoLei] = []
        self.estrutura = {
            'partes': [],
            'livros': [],
            'titulos': [],
            'capitulos': [],
            'secoes': [],
        }

    def parse(self) -> Tuple[List[ElementoLei], Dict]:
        """Executa o parse completo"""
        # Navega pela estrutura hasPart
        self._parse_parte(self.dados.get('hasPart', {}), [])
        return self.artigos, self.estrutura

    def _parse_parte(self, parte: Any, contexto: List[str]):
        """Parse recursivo de hasPart"""
        if isinstance(parte, dict):
            self._processar_elemento(parte, contexto)
        elif isinstance(parte, list):
            for item in parte:
                self._parse_parte(item, contexto)

    def _processar_elemento(self, elem: Dict, contexto: List[str]):
        """Processa um elemento individual"""
        # Extrai informações do workExample (versão atual)
        work_example = elem.get('workExample', {})
        if isinstance(work_example, list):
            # Pega a versão mais recente (última da lista)
            work_example = work_example[-1] if work_example else {}

        nome = work_example.get('name', elem.get('name', ''))
        texto = work_example.get('text', '')
        urn = elem.get('legislationIdentifier', '')

        # Identifica tipo pelo URN ou nome
        tipo = self._identificar_tipo(urn, nome)

        # Atualiza estrutura hierárquica
        novo_contexto = contexto.copy()
        if tipo in ['parte', 'livro', 'titulo', 'capitulo', 'secao']:
            titulo_completo = f"{nome} - {texto}" if texto else nome
            novo_contexto.append(titulo_completo)

            # Adiciona à estrutura
            chave = tipo + 's' if tipo != 'secao' else 'secoes'
            if chave in self.estrutura:
                self.estrutura[chave].append(titulo_completo)

        # Se for artigo, cria ElementoLei
        if tipo == 'artigo':
            artigo = self._criar_artigo(elem, novo_contexto)
            self.artigos.append(artigo)

        # Processa filhos
        filhos = elem.get('hasPart', [])
        if isinstance(filhos, dict):
            filhos = [filhos]

        for filho in filhos:
            self._parse_parte(filho, novo_contexto)

    def _identificar_tipo(self, urn: str, nome: str) -> str:
        """Identifica o tipo do elemento"""
        urn_lower = urn.lower()
        nome_lower = nome.lower()

        if '_cpt' in urn_lower or 'caput' in urn_lower:
            return 'caput'
        if '_par' in urn_lower or 'parágrafo' in nome_lower or '§' in nome:
            return 'paragrafo'
        if '_inc' in urn_lower or re.match(r'^[IVX]+\s*[-–]', nome):
            return 'inciso'
        if '_ali' in urn_lower or re.match(r'^[a-z]\s*\)', nome):
            return 'alinea'
        if '_ite' in urn_lower:
            return 'item'
        if '!art' in urn_lower or nome_lower.startswith('art'):
            return 'artigo'
        if '!prt' in urn_lower or 'parte' in nome_lower:
            return 'parte'
        if '!liv' in urn_lower or 'livro' in nome_lower:
            return 'livro'
        if '!tit' in urn_lower or 'título' in nome_lower:
            return 'titulo'
        if '!cap' in urn_lower or 'capítulo' in nome_lower:
            return 'capitulo'
        if '!sec' in urn_lower or 'seção' in nome_lower:
            return 'secao'

        return 'desconhecido'

    def _criar_artigo(self, elem: Dict, contexto: List[str]) -> ElementoLei:
        """Cria um ElementoLei do tipo artigo"""
        work = elem.get('workExample', {})
        if isinstance(work, list):
            work = work[-1] if work else {}

        # Extrai número do artigo
        nome = work.get('name', '')
        numero_match = re.search(r'(\d+[º°]?(?:-?[A-Za-z])?)', nome)
        numero = numero_match.group(1) if numero_match else '0'
        numero = numero.replace('º', '').replace('°', '')

        # Verifica vigência
        legal_force = work.get('legislationLegalForce', '')
        vigente = legal_force != 'NotInForce'

        artigo = ElementoLei(
            tipo='artigo',
            numero=numero,
            texto='',  # Será preenchido pelos filhos
            epigrafe='',
            urn=elem.get('legislationIdentifier', ''),
            vigente=vigente
        )

        # Processa filhos (caput, parágrafos, etc.)
        filhos = elem.get('hasPart', [])
        if isinstance(filhos, dict):
            filhos = [filhos]

        for filho in filhos:
            elem_filho = self._processar_filho_artigo(filho)
            if elem_filho:
                artigo.filhos.append(elem_filho)

        return artigo

    def _processar_filho_artigo(self, elem: Dict) -> Optional[ElementoLei]:
        """Processa um filho de artigo (caput, parágrafo, inciso, etc.)"""
        work = elem.get('workExample', {})
        if isinstance(work, list):
            work = work[-1] if work else {}

        nome = work.get('name', elem.get('name', ''))
        texto = work.get('text', '')
        urn = elem.get('legislationIdentifier', '')

        tipo = self._identificar_tipo(urn, nome)

        if tipo == 'desconhecido':
            return None

        # Extrai número
        numero = ''
        if tipo == 'paragrafo':
            match = re.search(r'§\s*(\d+|único)', nome, re.IGNORECASE)
            numero = match.group(1) if match else 'unico'
        elif tipo == 'inciso':
            match = re.search(r'^([IVX]+)', nome)
            numero = match.group(1) if match else ''
        elif tipo == 'alinea':
            match = re.search(r'^([a-z])', nome, re.IGNORECASE)
            numero = match.group(1).lower() if match else ''

        # Verifica vigência
        legal_force = work.get('legislationLegalForce', '')
        vigente = legal_force != 'NotInForce'

        elemento = ElementoLei(
            tipo=tipo,
            numero=numero,
            texto=texto,
            urn=urn,
            vigente=vigente
        )

        # Processa filhos recursivamente
        filhos = elem.get('hasPart', [])
        if isinstance(filhos, dict):
            filhos = [filhos]

        for filho in filhos:
            elem_filho = self._processar_filho_artigo(filho)
            if elem_filho:
                elemento.filhos.append(elem_filho)

        return elemento


# =============================================================================
# PARSER HTML (FALLBACK) - v2.0 com parsing direto de elementos HTML
# =============================================================================

class ParserHTMLNormas:
    """
    Parser para HTML do binário do normas.leg.br

    Processa diretamente os elementos <p> do HTML, identificando:
    - Artigos: <span style="font-weight:bold">Art</span><span>. 121...</span>
    - Epígrafes: <span style="font-weight:bold">Texto em negrito</span> (sem "Art")
    - Parágrafos: texto começando com "§" ou "Parágrafo único"
    - Incisos: texto começando com numerais romanos (I -, II -)
    - Alíneas: texto começando com letra minúscula e parênteses (a), b))
    - Penas: texto começando com "Pena -"
    """

    # Regex para identificar elementos pelo texto
    # Captura: Art. 1, Art. 1°, Art. 1º, Art. 121-A, Art. 121-B
    # Funciona com formato "Art. 1ºTexto" (sem espaço após ordinal)
    # (?:-[A-Za-z])? captura hífen+letra apenas se houver hífen (artigos como 121-A)
    RE_ARTIGO = re.compile(r'^Art\.?\s*(\d+[º°]?(?:-[A-Za-z])?)', re.IGNORECASE)
    RE_PARAGRAFO = re.compile(r'^§\s*(\d+[º°]?(?:\s*-[A-Za-z])?)\.?\s*', re.IGNORECASE)
    RE_PARAGRAFO_UNICO = re.compile(r'^Parágrafo\s+único\.?\s*', re.IGNORECASE)
    RE_INCISO = re.compile(r'^([IVX]+)\s*[-–—]\s*', re.IGNORECASE)
    RE_ALINEA = re.compile(r'^([a-z])\s*\)\s*', re.IGNORECASE)
    RE_ITEM = re.compile(r'^(\d+)\s*[-–—.]\s*', re.IGNORECASE)
    RE_PENA = re.compile(r'^Pena\s*[-–—]\s*', re.IGNORECASE)

    # Regex para estrutura hierárquica
    RE_PARTE = re.compile(r'^PARTE\s+(GERAL|ESPECIAL|[IVX]+)', re.IGNORECASE)
    RE_LIVRO = re.compile(r'^LIVRO\s+([IVX]+)', re.IGNORECASE)
    RE_TITULO = re.compile(r'^T[ÍI]TULO\s+([IVX]+)', re.IGNORECASE)
    RE_CAPITULO = re.compile(r'^CAP[ÍI]TULO\s+([IVX]+)', re.IGNORECASE)
    RE_SECAO = re.compile(r'^SE[ÇC][ÃA]O\s+([IVX]+)', re.IGNORECASE)

    def __init__(self, html: str):
        # Corrige possível double-encoding UTF-8
        self.html = self._corrigir_encoding(html)
        self.soup = None
        self.artigos: List[ElementoLei] = []
        self.estrutura = {
            'partes': [],
            'livros': [],
            'titulos': [],
            'capitulos': [],
            'secoes': [],
        }
        self.contexto_atual = []
        self.epigrafe_pendente = ""  # Epígrafe aguardando artigo
        self.estrutura_iniciada = False  # Flag para ignorar headers antes do primeiro elemento estrutural

    def _corrigir_encoding(self, html: str) -> str:
        """
        Corrige double-encoding UTF-8 comum em HTMLs do normas.leg.br

        O HTML às vezes é salvo com encoding UTF-8 duplo, fazendo com que
        caracteres como § (U+00A7) apareçam como Â§ (dois caracteres).
        """
        try:
            # Tenta corrigir double-encoding: UTF-8 -> latin-1 -> UTF-8
            return html.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            # Se falhar, retorna o original
            return html

    def parse(self) -> Tuple[List[ElementoLei], Dict]:
        """Executa o parse completo processando elementos HTML"""
        if not BS4_AVAILABLE:
            raise ImportError("beautifulsoup4 não instalado")

        self.soup = BeautifulSoup(self.html, 'html.parser')

        # Remove scripts e styles
        for tag in self.soup(['script', 'style']):
            tag.decompose()

        # Processa elementos <p>, <h3>, <h4> (h3/h4 podem conter epígrafes)
        paragrafos = self.soup.find_all(['p', 'h3', 'h4'])

        artigo_atual = None
        ultimo_paragrafo = None  # Último parágrafo do artigo atual
        ultimo_inciso = None     # Último inciso (pode ser do caput ou de um parágrafo)
        ultimo_contexto = None   # 'caput', 'paragrafo', 'inciso'
        rubrica_pendente = None  # Rubrica aguardando associação a parágrafo/inciso
        estrutura_pendente = None  # Título/Capítulo aguardando nome da próxima linha

        # Rastreamento do path atual para cada artigo
        path_atual = {
            'parte': '',
            'livro': '',
            'titulo': '',
            'capitulo': '',
            'secao': ''
        }

        # Pré-scan: verifica se existe menção a "Parte Geral" ou "Parte Especial" no HTML
        texto_completo_html = ' '.join(p.get_text(strip=True) for p in paragrafos)
        tem_parte_geral = 'Parte Geral' in texto_completo_html or 'PARTE GERAL' in texto_completo_html
        tem_parte_especial = 'PARTE ESPECIAL' in texto_completo_html.upper()
        parte_geral_adicionada = False

        for p in paragrafos:
            # Analisa o elemento <p>
            info = self._analisar_paragrafo(p)

            if info['tipo'] == 'estrutura':
                # Parte, Livro, Título, Capítulo, Seção
                subtipo = info.get('subtipo', '')

                # Adiciona "Parte geral" implicitamente antes do primeiro TÍTULO
                # (apenas se o HTML menciona "Parte Geral" em algum lugar)
                if subtipo == 'titulo' and tem_parte_geral and not parte_geral_adicionada:
                    self.estrutura['partes'].append('Parte geral')
                    path_atual['parte'] = 'Parte geral'
                    parte_geral_adicionada = True

                # Guarda estrutura pendente para combinar com nome da próxima linha
                if subtipo in ('titulo', 'capitulo', 'secao'):
                    estrutura_pendente = info
                else:
                    self._adicionar_estrutura(info)
                    # Atualiza path_atual
                    if subtipo == 'parte':
                        path_atual['parte'] = info['texto']
                        path_atual['titulo'] = ''
                        path_atual['capitulo'] = ''
                        path_atual['secao'] = ''
                    elif subtipo == 'livro':
                        path_atual['livro'] = info['texto']

                self.estrutura_iniciada = True  # Marca que a estrutura da lei começou
                continue

            # Se há estrutura pendente e a linha atual é o nome dela
            if estrutura_pendente and info['tipo'] in ('continuacao', 'vazio'):
                texto_nome = info.get('texto', '').strip()
                if texto_nome and info['tipo'] != 'vazio':
                    # Combina: "TÍTULO I" + " - " + "DA APLICAÇÃO DA LEI PENAL"
                    texto_completo = f"{estrutura_pendente['texto']} - {texto_nome}"
                    estrutura_pendente['texto'] = texto_completo
                self._adicionar_estrutura(estrutura_pendente)

                # Atualiza path_atual para título, capítulo, seção
                subtipo = estrutura_pendente.get('subtipo', '')
                if subtipo == 'titulo':
                    path_atual['titulo'] = estrutura_pendente['texto']
                    path_atual['capitulo'] = ''
                    path_atual['secao'] = ''
                elif subtipo == 'capitulo':
                    path_atual['capitulo'] = estrutura_pendente['texto']
                    path_atual['secao'] = ''
                elif subtipo == 'secao':
                    path_atual['secao'] = estrutura_pendente['texto']

                estrutura_pendente = None
                continue

            if info['tipo'] == 'epigrafe':
                # Só captura epígrafes APÓS o primeiro elemento estrutural
                # (ignora headers institucionais como "CÂMARA DOS DEPUTADOS")
                if self.estrutura_iniciada:
                    self.epigrafe_pendente = info['texto']
                    # Se estamos dentro de um artigo, cria rubrica pendente
                    if artigo_atual:
                        rubrica_pendente = ElementoLei(
                            tipo='rubrica',
                            numero='',
                            texto=info['texto'],
                            vigente=True
                        )
                continue

            if info['tipo'] == 'artigo':
                # Salva artigo anterior
                if artigo_atual:
                    self.artigos.append(artigo_atual)

                artigo_atual = ElementoLei(
                    tipo='artigo',
                    numero=info['numero'],
                    texto=info['texto'],  # Texto do caput
                    epigrafe=self.epigrafe_pendente,
                    vigente=not self._is_revogado(info['texto']),
                    path=path_atual.copy()  # Copia o path atual
                )
                self.epigrafe_pendente = ""
                rubrica_pendente = None  # Reset rubrica ao mudar de artigo
                ultimo_paragrafo = None
                ultimo_inciso = None
                ultimo_contexto = 'caput'
                continue

            if not artigo_atual:
                continue

            if info['tipo'] == 'paragrafo':
                # Se há rubrica pendente, adiciona antes do parágrafo
                if rubrica_pendente:
                    rubrica_pendente.numero = info['numero']  # Associa ao número do parágrafo
                    artigo_atual.filhos.append(rubrica_pendente)
                    rubrica_pendente = None

                paragrafo = ElementoLei(
                    tipo='paragrafo',
                    numero=info['numero'],
                    texto=info['texto'],
                    vigente=not self._is_revogado(info['texto'])
                )
                artigo_atual.filhos.append(paragrafo)
                ultimo_paragrafo = paragrafo
                ultimo_inciso = None
                ultimo_contexto = 'paragrafo'
                continue

            if info['tipo'] == 'inciso':
                # Se há rubrica pendente, adiciona antes do inciso
                if rubrica_pendente:
                    rubrica_pendente.numero = info['numero']  # Associa ao número do inciso (romano)
                    if ultimo_paragrafo:
                        ultimo_paragrafo.filhos.append(rubrica_pendente)
                    else:
                        artigo_atual.filhos.append(rubrica_pendente)
                    rubrica_pendente = None

                inciso = ElementoLei(
                    tipo='inciso',
                    numero=info['numero'],
                    texto=info['texto'],
                    vigente=not self._is_revogado(info['texto'])
                )

                # Adiciona ao parágrafo atual ou diretamente ao artigo (caput)
                if ultimo_paragrafo:
                    ultimo_paragrafo.filhos.append(inciso)
                else:
                    artigo_atual.filhos.append(inciso)

                ultimo_inciso = inciso
                ultimo_contexto = 'inciso'
                continue

            if info['tipo'] == 'alinea':
                alinea = ElementoLei(
                    tipo='alinea',
                    numero=info['numero'],
                    texto=info['texto'],
                    vigente=not self._is_revogado(info['texto'])
                )

                # Alíneas são filhas do último inciso
                if ultimo_inciso:
                    ultimo_inciso.filhos.append(alinea)
                elif ultimo_paragrafo:
                    # Fallback: adiciona ao parágrafo
                    ultimo_paragrafo.filhos.append(alinea)
                else:
                    # Fallback: adiciona ao artigo
                    artigo_atual.filhos.append(alinea)
                continue

            if info['tipo'] == 'item':
                item = ElementoLei(
                    tipo='item',
                    numero=info['numero'],
                    texto=info['texto'],
                    vigente=not self._is_revogado(info['texto'])
                )

                # Items são filhos de alíneas ou incisos
                if ultimo_inciso and ultimo_inciso.filhos:
                    ultima_alinea = ultimo_inciso.filhos[-1]
                    if ultima_alinea.tipo == 'alinea':
                        ultima_alinea.filhos.append(item)
                    else:
                        ultimo_inciso.filhos.append(item)
                elif ultimo_inciso:
                    ultimo_inciso.filhos.append(item)
                continue

            if info['tipo'] == 'pena':
                # Pena é um elemento separado mas associado ao contexto
                pena = ElementoLei(
                    tipo='pena',
                    numero='',
                    texto=info['texto'],
                    vigente=True
                )

                # Adiciona como filho do contexto apropriado
                if ultimo_contexto == 'inciso' and ultimo_inciso:
                    # Pena após inciso (como no Art. 121, §2º, V)
                    # Adiciona ao pai do inciso
                    if ultimo_paragrafo:
                        ultimo_paragrafo.filhos.append(pena)
                    else:
                        artigo_atual.filhos.append(pena)
                elif ultimo_paragrafo:
                    ultimo_paragrafo.filhos.append(pena)
                else:
                    artigo_atual.filhos.append(pena)
                continue

            if info['tipo'] == 'continuacao':
                # Linha de continuação - anexa ao último elemento
                texto_extra = info['texto']
                if ultimo_contexto == 'inciso' and ultimo_inciso:
                    ultimo_inciso.texto += ' ' + texto_extra
                elif ultimo_contexto == 'paragrafo' and ultimo_paragrafo:
                    ultimo_paragrafo.texto += ' ' + texto_extra
                elif ultimo_contexto == 'caput' and artigo_atual:
                    artigo_atual.texto += ' ' + texto_extra

        # Salva último artigo
        if artigo_atual:
            self.artigos.append(artigo_atual)

        return self.artigos, self.estrutura

    def _analisar_paragrafo(self, p) -> Dict[str, Any]:
        """
        Analisa um elemento <p> e identifica seu tipo e conteúdo

        Returns:
            Dict com 'tipo', 'numero', 'texto'
        """
        # Extrai texto completo
        texto_completo = p.get_text(strip=True)

        if not texto_completo:
            return {'tipo': 'vazio', 'texto': '', 'numero': ''}

        # Verifica se é estrutura hierárquica (H1/H2 style ou uppercase)
        texto_upper = texto_completo.strip()

        if self.RE_PARTE.match(texto_upper):
            return {'tipo': 'estrutura', 'subtipo': 'parte', 'texto': texto_completo}
        if self.RE_LIVRO.match(texto_upper):
            return {'tipo': 'estrutura', 'subtipo': 'livro', 'texto': texto_completo}
        if self.RE_TITULO.match(texto_upper):
            return {'tipo': 'estrutura', 'subtipo': 'titulo', 'texto': texto_completo}
        if self.RE_CAPITULO.match(texto_upper):
            return {'tipo': 'estrutura', 'subtipo': 'capitulo', 'texto': texto_completo}
        if self.RE_SECAO.match(texto_upper):
            return {'tipo': 'estrutura', 'subtipo': 'secao', 'texto': texto_completo}

        # Tags h3/h4 são geralmente epígrafes (ex: "Anterioridade da lei")
        if p.name in ('h3', 'h4'):
            # Verifica se não é estrutura ou artigo
            if not self.RE_ARTIGO.match(texto_completo):
                return {'tipo': 'epigrafe', 'texto': texto_completo, 'numero': ''}

        # Verifica estrutura do HTML para identificar epígrafe
        spans = p.find_all('span')
        is_epigrafe = False

        if spans:
            # Verifica se o texto principal está em negrito
            texto_bold = ''
            texto_normal = ''

            for span in spans:
                style = span.get('style', '')
                span_text = span.get_text(strip=True)

                if 'font-weight:bold' in style or 'font-weight: bold' in style:
                    texto_bold += span_text
                else:
                    texto_normal += span_text

            # É epígrafe se:
            # 1. Todo texto em negrito e NÃO começa com "Art", ou
            # 2. Texto bold + referência legislativa (ex: "Feminicídio(Nome jurídico...Lei nº...)")
            if texto_bold and not texto_bold.strip().startswith('Art'):
                is_nome_juridico = texto_normal and (
                    'Nome jurídico' in texto_normal or
                    '(Incluíd' in texto_normal or
                    '(Acrescid' in texto_normal
                )

                if not texto_normal or is_nome_juridico:
                    # Verifica se não é um elemento de conteúdo
                    if not self.RE_PARAGRAFO.match(texto_bold):
                        if not self.RE_INCISO.match(texto_bold):
                            if not self.RE_ALINEA.match(texto_bold):
                                is_epigrafe = True

        if is_epigrafe:
            return {'tipo': 'epigrafe', 'texto': texto_completo, 'numero': ''}

        # Verifica se é artigo (Art em negrito seguido de número)
        match_art = self.RE_ARTIGO.match(texto_completo)
        if match_art:
            numero = match_art.group(1).replace('º', '').replace('°', '')
            texto = texto_completo[match_art.end():].strip()
            return {'tipo': 'artigo', 'numero': numero, 'texto': texto}

        # Verifica parágrafo
        match_par = self.RE_PARAGRAFO.match(texto_completo)
        if match_par:
            numero = match_par.group(1)
            # Normaliza para slug: remove ordinal e espaços (ex: "2º -A" -> "2-A")
            numero = numero.replace('º', '').replace('°', '')
            numero = re.sub(r'\s+', '', numero)
            texto = texto_completo[match_par.end():].strip()
            return {'tipo': 'paragrafo', 'numero': numero, 'texto': texto}

        match_pu = self.RE_PARAGRAFO_UNICO.match(texto_completo)
        if match_pu:
            texto = texto_completo[match_pu.end():].strip()
            return {'tipo': 'paragrafo', 'numero': 'unico', 'texto': texto}

        # Verifica inciso
        match_inc = self.RE_INCISO.match(texto_completo)
        if match_inc:
            numero = match_inc.group(1).upper()
            texto = texto_completo[match_inc.end():].strip()
            return {'tipo': 'inciso', 'numero': numero, 'texto': texto}

        # Verifica alínea
        match_ali = self.RE_ALINEA.match(texto_completo)
        if match_ali:
            numero = match_ali.group(1).lower()
            texto = texto_completo[match_ali.end():].strip()
            return {'tipo': 'alinea', 'numero': numero, 'texto': texto}

        # Verifica item (números seguidos de ponto/traço)
        match_item = self.RE_ITEM.match(texto_completo)
        if match_item:
            numero = match_item.group(1)
            texto = texto_completo[match_item.end():].strip()
            return {'tipo': 'item', 'numero': numero, 'texto': texto}

        # Verifica pena
        if self.RE_PENA.match(texto_completo):
            return {'tipo': 'pena', 'texto': texto_completo, 'numero': ''}

        # Linha de continuação ou texto solto
        # Verifica se começa com letra minúscula (continuação)
        if texto_completo and texto_completo[0].islower():
            return {'tipo': 'continuacao', 'texto': texto_completo, 'numero': ''}

        # Texto não identificado - trata como continuação
        return {'tipo': 'continuacao', 'texto': texto_completo, 'numero': ''}

    def _adicionar_estrutura(self, info: Dict):
        """Adiciona elemento de estrutura hierárquica"""
        subtipo = info.get('subtipo', '')
        texto = info.get('texto', '')

        if subtipo == 'parte':
            self.estrutura['partes'].append(texto)
            self.contexto_atual = [texto]
        elif subtipo == 'livro':
            self.estrutura['livros'].append(texto)
        elif subtipo == 'titulo':
            self.estrutura['titulos'].append(texto)
        elif subtipo == 'capitulo':
            self.estrutura['capitulos'].append(texto)
        elif subtipo == 'secao':
            self.estrutura['secoes'].append(texto)

    def _is_revogado(self, texto: str) -> bool:
        """Verifica se o texto indica revogação"""
        texto_lower = texto.lower()
        return '(revogad' in texto_lower or '(vetad' in texto_lower


# =============================================================================
# GERADOR DE OUTPUT
# =============================================================================

class GeradorOutput:
    """Gera o JSON de saída no formato do site"""

    def __init__(self, artigos: List[ElementoLei], estrutura: Dict, metadados: Dict):
        self.artigos = artigos
        self.estrutura = estrutura
        self.metadados = metadados
        self.conversor = ConversorURNSlug()

    def gerar(self) -> Dict[str, Any]:
        """Gera o JSON completo"""
        artigos_output = []

        for artigo in self.artigos:
            art_output = self._gerar_artigo(artigo)
            artigos_output.append(art_output)

        return {
            "lei": {
                "id": self._gerar_id_lei(),
                "nome": self.metadados.get('titulo', ''),
                "numero": self._extrair_numero_lei(),
                "ementa": self.metadados.get('ementa', ''),
                "urn": self.metadados.get('urn', ''),
                "estrutura": self.estrutura,
            },
            "artigos": artigos_output
        }

    def _is_revogado(self, texto: str) -> bool:
        """Verifica se o texto indica revogação"""
        texto_lower = texto.lower()
        return '(revogad' in texto_lower or '(vetad' in texto_lower

    def _gerar_id_lei(self) -> str:
        """Gera ID da lei a partir do URN"""
        urn = self.metadados.get('urn', '')
        # urn:lex:br:federal:decreto.lei:1940-12-07;2848 -> decreto-lei-2848
        match = re.search(r':([^:]+):[\d-]+;(\d+)', urn)
        if match:
            tipo = match.group(1).replace('.', '-')
            numero = match.group(2)
            return f"{tipo}-{numero}"
        return "lei-desconhecida"

    def _extrair_numero_lei(self) -> str:
        """Extrai número da lei do URN"""
        urn = self.metadados.get('urn', '')
        match = re.search(r';(\d+)', urn)
        return match.group(1) if match else ""

    # Mapeamento de números romanos para arábicos
    ROMANO_PARA_ARABICO = {
        'I': '1', 'II': '2', 'III': '3', 'IV': '4', 'V': '5',
        'VI': '6', 'VII': '7', 'VIII': '8', 'IX': '9', 'X': '10',
        'XI': '11', 'XII': '12', 'XIII': '13', 'XIV': '14', 'XV': '15',
        'XVI': '16', 'XVII': '17', 'XVIII': '18', 'XIX': '19', 'XX': '20',
    }

    def _romano_para_arabico(self, romano: str) -> str:
        """Converte número romano para arábico"""
        return self.ROMANO_PARA_ARABICO.get(romano.upper(), romano.lower())

    def _formatar_label_artigo(self, numero: str) -> str:
        """
        Formata o label do artigo conforme regras de redação legislativa.

        - Art. 1º a 9º: ordinal com símbolo
        - Art. 10 em diante: cardinal sem símbolo
        - Sufixos (-A, -B, etc.) preservados após o ordinal/cardinal
        """
        # Extrai número base e sufixo (ex: "359-U" → "359", "-U")
        match = re.match(r'^(\d+)(-[A-Za-z])?$', numero)

        if not match:
            return f"Art. {numero}"

        num_base = int(match.group(1))
        sufixo = match.group(2) or ""

        if num_base <= 9:
            return f"Art. {num_base}º{sufixo}"
        else:
            return f"Art. {num_base}{sufixo}"

    # Regex para capturar anotações no FINAL do texto
    # Usa padrões que funcionam com ou sem acentos
    # O $ ancora no final e o + captura múltiplas anotações consecutivas
    # Padrão: palavra de ação legislativa + "pel[ao]" em qualquer posição dentro do parêntese
    RE_ANOTACOES_FINAL = re.compile(
        r'(\s*\((?=[^)]*(?:(?:inclu[íi]d|revogad|acrescid|alterad|vetad|suprimi|renumerad)[oa]?.*pel[ao]|reda[çc][ãa]o\s+dad|vide|vig[êe]ncia))[^)]+\))+$',
        re.IGNORECASE
    )

    # Regex para separar anotações individuais
    RE_ANOTACAO_INDIVIDUAL = re.compile(
        r'\([^)]+\)'
    )

    def _separar_anotacoes(self, texto: str) -> Tuple[str, str, List[str]]:
        """
        Separa anotações legislativas do texto.

        Returns:
            Tuple[texto_limpo, texto_original, anotacoes]
        """
        if not texto:
            return '', '', []

        # Busca bloco de anotações no final
        match = self.RE_ANOTACOES_FINAL.search(texto)
        if not match:
            return texto, texto, []

        # Extrai o bloco de anotações
        bloco_anotacoes = match.group()
        texto_limpo = texto[:match.start()].strip()

        # Separa anotações individuais do bloco
        anotacoes = [a.strip() for a in self.RE_ANOTACAO_INDIVIDUAL.findall(bloco_anotacoes)]

        return texto_limpo, texto, anotacoes

    def _gerar_artigo(self, artigo: ElementoLei) -> Dict[str, Any]:
        """Gera output de um artigo"""
        # Gera plate_content
        plate_content = []
        textos = []  # Para texto_plano (SEM epígrafe)

        # Slug base do artigo
        slug_base = f"artigo-{artigo.numero}"

        # URN do artigo
        urn_artigo = artigo.urn

        # Epígrafe do artigo (se existir) - NÃO adiciona ao textos[]
        epigrafe_limpa = ""  # Para usar no return
        if artigo.epigrafe:
            # Separa anotações da epígrafe
            epigrafe_limpa, epigrafe_original, anotacoes_epigrafe = self._separar_anotacoes(artigo.epigrafe)
            plate_content.append({
                "type": "p",
                "children": [
                    {"text": epigrafe_limpa, "bold": True}
                ],
                "id": str(uuid_lib.uuid4()),
                "slug": f"{slug_base}_epigrafe",
                "search_text": epigrafe_limpa,
                "texto_original": epigrafe_original if anotacoes_epigrafe else None,
                "anotacoes": anotacoes_epigrafe if anotacoes_epigrafe else None
            })

        # Caput - formata conforme regras de redação legislativa
        label = self._formatar_label_artigo(artigo.numero)
        texto_caput_original = artigo.texto

        # Separa anotações do caput
        texto_caput_limpo, _, anotacoes_caput = self._separar_anotacoes(texto_caput_original)

        if texto_caput_original:
            # Usa texto limpo para exibição e busca
            caput_completo_limpo = f"{label} {texto_caput_limpo}"
            caput_completo_original = f"{label} {texto_caput_original}"
            textos.append(caput_completo_limpo)

            plate_content.append({
                "type": "p",
                "children": [
                    {"text": label + " ", "bold": True},
                    {"text": texto_caput_limpo}
                ],
                "id": str(uuid_lib.uuid4()),
                "slug": "caput",
                "urn": f"{urn_artigo}_cpt" if urn_artigo else "",
                "search_text": caput_completo_limpo,
                "texto_original": caput_completo_original if anotacoes_caput else None,
                "anotacoes": anotacoes_caput if anotacoes_caput else None
            })

        # Processa filhos
        self._processar_filhos_plate(
            artigo.filhos,
            plate_content,
            textos,
            slug_base,
            urn_artigo,
            indent=0
        )

        # Monta output
        texto_plano = '\n'.join(textos)
        content_hash = hashlib.md5(texto_plano.encode('utf-8')).hexdigest()

        # Verifica vigência usando texto original (com anotações) ou anotações do caput
        vigente = artigo.vigente
        if not vigente:
            # Dupla verificação: checa nas anotações do caput se há revogação/veto
            anotacoes_lower = ' '.join(anotacoes_caput).lower() if anotacoes_caput else ''
            texto_original_lower = texto_caput_original.lower() if texto_caput_original else ''
            if '(revogad' not in anotacoes_lower and '(vetad' not in anotacoes_lower:
                if '(revogad' not in texto_original_lower and '(vetad' not in texto_original_lower:
                    vigente = True

        # Gera contexto e path a partir do path do artigo
        path = artigo.path if artigo.path else {}
        path_filtrado = {k: v for k, v in path.items() if v}  # Remove valores vazios
        contexto = " > ".join(path_filtrado.values()) if path_filtrado else ""

        return {
            "id": slug_base,
            "numero": artigo.numero,
            "slug": slug_base,
            "epigrafe": epigrafe_limpa,
            "plate_content": plate_content,
            "texto_plano": texto_plano,
            "search_text": texto_plano,
            "vigente": vigente,
            "contexto": contexto,
            "path": path_filtrado,
            "content_hash": content_hash
            # Nota: 'urn' removido pois não existe na tabela Supabase
        }

    def _processar_filhos_plate(
        self,
        filhos: List[ElementoLei],
        plate_content: List[Dict],
        textos: List[str],
        slug_base: str,
        urn_base: str,
        indent: int,
        contexto_paragrafo: str = ""  # Para rastrear parágrafo atual para slugs
    ):
        """Processa filhos recursivamente para plate_content"""
        for filho in filhos:
            # Gera slug e label baseado no tipo
            if filho.tipo == 'paragrafo':
                slug_filho = f"{slug_base}.paragrafo-{filho.numero}"
                # Label com ordinal para números 1-9 (§ 2º-A, § 10-B)
                if filho.numero == 'unico':
                    label = "Parágrafo único"
                else:
                    match = re.match(r'^(\d+)(-.+)?$', filho.numero)
                    if match:
                        base = int(match.group(1))
                        suffix = match.group(2) or ''
                        label = f"§ {base}º{suffix}" if base <= 9 else f"§ {base}{suffix}"
                    else:
                        label = f"§ {filho.numero}"
                urn_filho = f"{urn_base}_par{filho.numero}" if urn_base else ""
                novo_contexto = slug_filho  # Incisos usarão este contexto
            elif filho.tipo == 'inciso':
                # Converte romano para arábico para o slug
                numero_arabico = self._romano_para_arabico(filho.numero)
                # Usa contexto do parágrafo se existir
                base_para_inciso = contexto_paragrafo if contexto_paragrafo else slug_base
                slug_filho = f"{base_para_inciso}.inciso-{numero_arabico}"
                label = f"{filho.numero} -"
                urn_filho = f"{urn_base}_inc{numero_arabico}" if urn_base else ""
                novo_contexto = ""  # Reset contexto
            elif filho.tipo == 'alinea':
                slug_filho = f"{slug_base}.alinea-{filho.numero}"
                label = f"{filho.numero})"
                urn_filho = f"{urn_base}_ali{filho.numero}" if urn_base else ""
                novo_contexto = ""
            elif filho.tipo == 'item':
                slug_filho = f"{slug_base}.item-{filho.numero}"
                label = f"{filho.numero}."
                urn_filho = f"{urn_base}_ite{filho.numero}" if urn_base else ""
                novo_contexto = ""
            elif filho.tipo == 'pena':
                # Pena - usa slug do contexto atual
                slug_pena = f"{contexto_paragrafo}.penalty" if contexto_paragrafo else f"{slug_base}.penalty"

                # Separa anotações da pena
                texto_pena_limpo, texto_pena_original, anotacoes_pena = self._separar_anotacoes(filho.texto)
                textos.append(texto_pena_limpo)

                # Separa "Pena" do resto do texto
                texto_apos_pena = texto_pena_limpo
                if texto_pena_limpo.lower().startswith('pena'):
                    texto_apos_pena = texto_pena_limpo[4:].lstrip(' -–—')

                plate_content.append({
                    "type": "p",
                    "children": [
                        {"text": "Pena ", "bold": True},
                        {"text": texto_apos_pena}
                    ],
                    "id": str(uuid_lib.uuid4()),
                    "slug": slug_pena,
                    "search_text": texto_pena_limpo,
                    "texto_original": texto_pena_original if anotacoes_pena else None,
                    "anotacoes": anotacoes_pena if anotacoes_pena else None,
                    "indent": indent + 1
                })
                continue  # Penas não têm filhos
            elif filho.tipo == 'rubrica':
                # Rubrica/subtítulo - associa ao próximo elemento (parágrafo ou inciso)
                # O número do elemento foi guardado em filho.numero durante o parse
                idx_atual = filhos.index(filho)
                proximo_elemento = None
                for prox in filhos[idx_atual + 1:]:
                    if prox.tipo in ('paragrafo', 'inciso'):
                        proximo_elemento = prox
                        break

                # Gera slug no formato do original: paragrafo-X-epigraph ou inciso-X-epigraph
                if proximo_elemento:
                    if proximo_elemento.tipo == 'paragrafo':
                        slug_rubrica = f"{slug_base}.paragrafo-{proximo_elemento.numero}-epigraph"
                    else:  # inciso
                        numero_arabico = self._romano_para_arabico(proximo_elemento.numero)
                        slug_rubrica = f"{slug_base}.inciso-{numero_arabico}-epigraph"
                elif filho.numero:
                    # Usa o número armazenado no parse (pode ser romano se inciso)
                    numero_arabico = self._romano_para_arabico(filho.numero)
                    # Se o número é arábico, provavelmente é parágrafo
                    if filho.numero.isdigit() or filho.numero == 'unico':
                        slug_rubrica = f"{slug_base}.paragrafo-{filho.numero}-epigraph"
                    else:
                        slug_rubrica = f"{slug_base}.inciso-{numero_arabico}-epigraph"
                else:
                    slug_rubrica = f"{slug_base}.rubrica"

                # Separa anotações da rubrica
                texto_rubrica_limpo, texto_rubrica_original, anotacoes_rubrica = self._separar_anotacoes(filho.texto)
                textos.append(texto_rubrica_limpo)

                plate_content.append({
                    "type": "p",
                    "children": [
                        {"text": texto_rubrica_limpo, "bold": True, "italic": True}
                    ],
                    "id": str(uuid_lib.uuid4()),
                    "slug": slug_rubrica,
                    "search_text": texto_rubrica_limpo,
                    "texto_original": texto_rubrica_original if anotacoes_rubrica else None,
                    "anotacoes": anotacoes_rubrica if anotacoes_rubrica else None,
                    "indent": indent
                })
                continue  # Rubricas não têm filhos
            else:
                continue

            # Separa anotações do texto do filho
            texto_limpo, texto_original, anotacoes = self._separar_anotacoes(filho.texto)

            # Verifica status do dispositivo baseado nas anotações
            texto_lower = filho.texto.lower()
            anotacoes_lower = ' '.join(anotacoes).lower() if anotacoes else ''

            # Lógica para determinar se é revogado/vetado (só quando texto_limpo está vazio)
            is_revogado = False
            is_vetado = False

            # Remove pontuação solitária para verificar se está "vazio"
            texto_sem_pontuacao = re.sub(r'^[\s\.\,\;\:\-]+$', '', texto_limpo.strip())

            if not texto_sem_pontuacao:
                # Texto vazio - verificar anotações
                # 1. "acrescid" + "revogad" → revogado
                # 2. Só "(revogad..." → revogado
                # 3. "vetad" sem "mantid" → vetado
                # 4. "vetad" + "mantid" → válido (veto derrubado)

                tem_acrescido = 'acrescid' in anotacoes_lower
                tem_revogado = 'revogad' in anotacoes_lower
                tem_vetado = 'vetad' in anotacoes_lower
                tem_mantido = 'mantid' in anotacoes_lower

                if tem_acrescido and tem_revogado:
                    is_revogado = True
                elif tem_revogado and not tem_acrescido:
                    is_revogado = True
                elif tem_vetado and not tem_mantido:
                    is_vetado = True
                # Se tem "vetad" + "mantid" → dispositivo válido, não marca

            # Se revogado ou vetado, substitui texto
            if is_revogado or is_vetado:
                texto_exibir = "Dispositivo revogado." if is_revogado else "Dispositivo vetado."
                texto_completo_limpo = f"{label} {texto_exibir}".strip()
                texto_completo_original = f"{label} {texto_original}".strip() if texto_original else texto_completo_limpo
                textos.append(texto_completo_limpo)

                # Bloco plate com estilo tachado e cinza
                plate_content.append({
                    "type": "p",
                    "children": [
                        {"text": label + " ", "bold": True, "strikethrough": True, "color": "#9ca3af"},
                        {"text": texto_exibir, "strikethrough": True, "color": "#9ca3af"}
                    ],
                    "id": str(uuid_lib.uuid4()),
                    "slug": slug_filho,
                    "urn": urn_filho,
                    "search_text": texto_completo_limpo,
                    "texto_original": texto_completo_original if anotacoes else None,
                    "anotacoes": anotacoes if anotacoes else None,
                    "indent": indent + 1,
                    "revogado": is_revogado,
                    "vetado": is_vetado
                })
            else:
                # Texto completo (usa versão limpa)
                texto_completo_limpo = f"{label} {texto_limpo}".strip()
                texto_completo_original = f"{label} {texto_original}".strip()
                textos.append(texto_completo_limpo)

                # Bloco plate - usa slug completo
                plate_content.append({
                    "type": "p",
                    "children": [
                        {"text": label + " ", "bold": True},
                        {"text": texto_limpo}
                    ],
                    "id": str(uuid_lib.uuid4()),
                    "slug": slug_filho,  # Slug completo com hierarquia
                    "urn": urn_filho,
                    "search_text": texto_completo_limpo,
                    "texto_original": texto_completo_original if anotacoes else None,
                    "anotacoes": anotacoes if anotacoes else None,
                    "indent": indent + 1
                })

            # Processa filhos recursivamente
            if filho.filhos:
                self._processar_filhos_plate(
                    filho.filhos,
                    plate_content,
                    textos,
                    slug_filho,
                    urn_filho,
                    indent + 1,
                    contexto_paragrafo=novo_contexto if filho.tipo == 'paragrafo' else contexto_paragrafo
                )


# =============================================================================
# FUNÇÃO PRINCIPAL
# =============================================================================

def importar_lei(urn: str = None, lei: str = None, output: str = None) -> Dict[str, Any]:
    """
    Importa uma lei do normas.leg.br.

    Args:
        urn: URN da lei (ex: urn:lex:br:federal:decreto.lei:1940-12-07;2848)
        lei: Nome curto da lei (ex: codigo-penal)
        output: Caminho do arquivo de saída

    Returns:
        Dict com o JSON da lei
    """
    # Resolve URN
    if lei and not urn:
        urn = LEIS_CONHECIDAS.get(lei.lower())
        if not urn:
            raise ValueError(f"Lei desconhecida: {lei}. Use --urn para especificar.")

    if not urn:
        raise ValueError("Especifique --urn ou --lei")

    if console:
        console.print(Panel(f"[bold]Importando Lei[/bold]\nURN: {urn}", border_style="blue"))

    # 1. Busca dados da API
    cliente = ClienteNormasLeg()
    resultado = cliente.buscar_lei(urn)

    if console:
        console.print(f"[green]OK[/green] Fonte: {resultado['tipo'].upper()}")

    # 2. Parse
    if resultado['tipo'] == 'json':
        parser = ParserJSONNormas(resultado['dados'])
    else:
        parser = ParserHTMLNormas(resultado['dados'])

    artigos, estrutura = parser.parse()

    if console:
        console.print(f"[green]OK[/green] Artigos encontrados: {len(artigos)}")

    # 3. Gera output
    gerador = GeradorOutput(artigos, estrutura, resultado['metadados'])
    output_json = gerador.gerar()

    # 4. Validação
    erros_conversao = 0
    for art in output_json['artigos']:
        for bloco in art['plate_content']:
            slug = bloco.get('slug', '')
            urn_bloco = bloco.get('urn', '')
            if urn_bloco and slug:
                if not ConversorURNSlug.validar_conversao(urn_bloco, slug):
                    erros_conversao += 1

    if erros_conversao > 0:
        if console:
            console.print(f"[yellow]!![/yellow] {erros_conversao} conversões URN->slug com possíveis problemas")
    else:
        if console:
            console.print(f"[green]OK[/green] Todas conversões URN->slug validadas")

    # 5. Salva
    if output:
        with open(output, 'w', encoding='utf-8') as f:
            json.dump(output_json, f, ensure_ascii=False, indent=2)
        if console:
            console.print(f"[green]OK[/green] Salvo em: {output}")

    # Estatísticas finais
    if console:
        table = Table(title="Resumo")
        table.add_column("Métrica", style="cyan")
        table.add_column("Valor", style="green")

        table.add_row("Lei", output_json['lei']['nome'])
        table.add_row("URN", output_json['lei']['urn'])
        table.add_row("Artigos", str(len(output_json['artigos'])))
        table.add_row("Partes", str(len(estrutura.get('partes', []))))
        table.add_row("Títulos", str(len(estrutura.get('titulos', []))))
        table.add_row("Capítulos", str(len(estrutura.get('capitulos', []))))

        console.print(table)

    return output_json


def main():
    parser = argparse.ArgumentParser(
        description="Importador de leis do normas.leg.br",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python importer_normas_leg.py --lei codigo-penal --output codigp_v2.json
  python importer_normas_leg.py --urn "urn:lex:br:federal:decreto.lei:1940-12-07;2848" -o cp.json

Leis conhecidas:
  codigo-penal, codigo-civil, clt, cdc, eca, ctb, constituicao
        """
    )

    parser.add_argument('--urn', help='URN da lei')
    parser.add_argument('--lei', help='Nome curto da lei (ex: codigo-penal)')
    parser.add_argument('-o', '--output', help='Arquivo de saída JSON')

    args = parser.parse_args()

    if not args.urn and not args.lei:
        parser.print_help()
        sys.exit(1)

    try:
        importar_lei(urn=args.urn, lei=args.lei, output=args.output)
    except Exception as e:
        if console:
            console.print(f"[red]Erro: {e}[/red]")
        else:
            print(f"Erro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
