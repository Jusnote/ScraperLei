#!/usr/bin/env python3
"""Interface interativa para importar leis do Planalto.

Fluxo:
1. Solicita o link oficial do Planalto.
2. Baixa o HTML correspondente.
3. Tenta inferir automaticamente o tipo da norma, número e data para montar a URN LexML.
4. Usa o pipeline existente (importer_normas_leg.importar_lei) para gerar o JSON final.
"""
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

try:
    import requests
except ImportError as exc:  # pragma: no cover - feedback rápido no CLI
    print("Dependência ausente: requests. Instale com 'pip install requests'.")
    raise

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - feedback rápido no CLI
    print("Dependência ausente: beautifulsoup4. Instale com 'pip install beautifulsoup4'.")
    raise

from importer_normas_leg import importar_lei

MESES = {
    "JANEIRO": "01",
    "FEVEREIRO": "02",
    "MARCO": "03",
    "ABRIL": "04",
    "MAIO": "05",
    "JUNHO": "06",
    "JULHO": "07",
    "AGOSTO": "08",
    "SETEMBRO": "09",
    "OUTUBRO": "10",
    "NOVEMBRO": "11",
    "DEZEMBRO": "12",
}

TIPOS = {
    "LEI": "lei",
    "LEI COMPLEMENTAR": "lei.complementar",
    "LEI DELEGADA": "lei.delegada",
    "DECRETO-LEI": "decreto.lei",
    "DECRETO": "decreto",
    "DECRETO LEGISLATIVO": "decreto.legislativo",
    "MEDIDA PROVISORIA": "medida.provisoria",
    "EMENDA CONSTITUCIONAL": "emenda.constitucional",
    "CONSTITUICAO": "constituicao",
}

HEADER_REGEX = re.compile(
    r"\b(LEI COMPLEMENTAR|LEI DELEGADA|LEI|DECRETO-LEI|DECRETO LEGISLATIVO|DECRETO|MEDIDA PROVIS\wRIA|EMENDA CONSTITUCIONAL|CONSTITUI\w+)"  # tipo
    r"\s+N[^\dA-Za-z]*\s*([\d\.-A-Z]+)"  # número
    r"\s*,\s*DE\s+(.+?\d{4})",  # trecho com a data
    re.IGNORECASE | re.DOTALL,
)

@dataclass
class InferenciaNorma:
    tipo_display: str
    tipo_slug: str
    numero: str
    data_iso: str
    urn: str


def strip_accents(value: str) -> str:
    return ''.join(ch for ch in unicodedata.normalize('NFKD', value) if not unicodedata.combining(ch))


def normalizar_numero(texto: str) -> str:
    texto = texto.replace('.', '').replace(',', '').strip()
    return texto


def parse_data_portugues(trecho: str) -> Optional[str]:
    if not trecho:
        return None
    trecho_norm = strip_accents(trecho).upper()
    trecho_norm = trecho_norm.replace('º', '').replace('ª', '')
    match = re.search(r"(\d{1,2})\s+DE\s+([A-ZÇÃ]+)\s+DE\s+(\d{4})", trecho_norm)
    if not match:
        return None
    dia = int(match.group(1))
    mes_nome = match.group(2).strip()
    ano = match.group(3)
    mes = MESES.get(mes_nome)
    if not mes:
        return None
    return f"{ano}-{int(mes):02d}-{dia:02d}"


def inferir_urn(html_text: str) -> Optional[InferenciaNorma]:
    texto = BeautifulSoup(html_text, 'html.parser').get_text(" ", strip=True)
    match = HEADER_REGEX.search(texto)
    if not match:
        return None
    tipo_raw, numero_raw, data_raw = match.groups()
    tipo_norm = strip_accents(tipo_raw).upper().strip()
    tipo_slug = TIPOS.get(tipo_norm)
    if not tipo_slug:
        return None
    numero = normalizar_numero(numero_raw)
    data_iso = parse_data_portugues(data_raw)
    if not data_iso:
        return None
    urn = f"urn:lex:br:federal:{tipo_slug}:{data_iso};{numero}"
    return InferenciaNorma(tipo_display=tipo_raw.strip(), tipo_slug=tipo_slug, numero=numero, data_iso=data_iso, urn=urn)


def _ler_local(path_like: str) -> Optional[str]:
    path = Path(path_like)
    if not path.exists():
        return None
    encodings = ('utf-8', 'latin-1', 'cp1252')
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", b"", 0, 1, f"Não foi possível decodificar {path_like}")


def download_html(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        if parsed.scheme == "file":
            local_path = unquote(parsed.path)
            if os.name == "nt" and local_path.startswith("/") and len(local_path) > 2 and local_path[2] == ":":
                local_path = local_path.lstrip("/")
        else:
            local_path = url
        conteudo = _ler_local(local_path)
        if conteudo is not None:
            return conteudo
    else:
        conteudo = _ler_local(url)
        if conteudo is not None:
            return conteudo
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.encoding or 'utf-8'
    return resp.text


def sugerir_nome_saida(url: str, inferencia: Optional[InferenciaNorma]) -> str:
    if inferencia:
        tipo = inferencia.tipo_slug.replace('.', '_')
        return f"{tipo}_{inferencia.numero}.json"
    nome = Path(urlparse(url).path).stem or 'lei'
    return f"{nome}.json"


def main() -> None:
    print("=== Importador Planalto -> JSON ===")
    link = input("Cole o link completo do Planalto: ").strip()
    if not link:
        print("Nenhum link fornecido. Encerrando.")
        return

    print("Baixando HTML...", end=' ', flush=True)
    html_text = download_html(link)
    print("ok!")

    inferencia = inferir_urn(html_text)
    if inferencia:
        print(f"URN inferida: {inferencia.urn}")
        urn = input("Confirme ou edite a URN [Enter para aceitar]: ").strip() or inferencia.urn
    else:
        print("Não foi possível inferir a URN automaticamente.")
        urn = input("Informe manualmente a URN LexML completa: ").strip()
        if not urn:
            print("URN obrigatória para gerar o JSON. Encerrando.")
            return

    arquivo_padrao = sugerir_nome_saida(link, inferencia)
    destino = input(f"Arquivo de saída [{arquivo_padrao}]: ").strip() or arquivo_padrao

    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html', encoding='utf-8') as tmp:
        tmp.write(html_text)
        temp_path = tmp.name

    try:
        importar_lei(urn=urn, output=destino, planalto_html=temp_path)
        print(f"JSON gerado com sucesso em: {destino}")
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f"Erro ao gerar JSON: {exc}")
        sys.exit(1)
