#!/usr/bin/env python3
"""
Gerenciador V2 - Focado no Novo Scraper Estrutural (Híbrido)
Este script gerencia o ciclo de vida completo usando a nova engine:
1. Scraping (Preservando hierarquia completa e identificando Itens inline)
2. Conversão (Flattening para formato Supabase com indentação correta)
3. Importação (Batch upsert com verificação de diff)
"""

import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.rule import Rule
from rich.traceback import install
from rich import box

install()

# Imports locais
try:
    from scraper_v2 import StructuralScraper
    from import_to_supabase import get_supabase_client
except ImportError as e:
    print(f"Erro ao importar módulos: {e}")
    print("Verifique se scraper_v2.py e import_to_supabase.py estão na mesma pasta.")
    sys.exit(1)

console = Console()
OUTPUT_DIR = Path(__file__).parent

# ============ HELPERS ============

def get_ordem_numerica(numero: str) -> float:
    """Calcula ordem numérica para ordenação correta (ex: 121-A > 121)"""
    clean_num = numero.replace('.', '')
    match = re.match(r'^(\d+)(.*)$', clean_num)
    if not match: return 0.0
    
    base = float(match.group(1))
    suffix = match.group(2).strip().upper().replace('-', '').replace('º', '')
    
    if not suffix: return base
        
    suffix_val = 0
    if len(suffix) == 1 and suffix.isalpha():
        suffix_val = ord(suffix) - ord('A') + 1
    elif suffix.isdigit():
        suffix_val = int(suffix)
    else:
        suffix_val = 999 
        
    return base + (suffix_val / 1000.0)

def show_header():
    console.clear()
    console.print(Panel.fit(
        "[bold green]Gerenciador V2 (Scraper Estrutural)[/bold green]\n"
        "[dim]Engine Híbrida: DOM Node-ID + Inline Text Parser[/dim]",
        border_style="green",
        padding=(1, 4)
    ))
    console.print()

# ============ FLUXOS ============

def flow_analyze():
    show_header()
    console.print(Rule("1. Analisar e Converter Lei", style="green"))
    console.print("[dim]Este processo lê o HTML bruto, extrai a árvore estrutural e prepara o JSON para o Supabase.[/dim]\n")

    path = Prompt.ask("[cyan]Caminho do arquivo HTML[/cyan]")
    if not os.path.exists(path):
        console.print("[red]❌ Arquivo não encontrado![/red]")
        Prompt.ask("Enter para voltar")
        return

    lei_id = Prompt.ask("[cyan]ID da Lei[/cyan] (ex: lei-9503, cc-2002)")
    lei_nome = Prompt.ask("[cyan]Nome Oficial[/cyan]", default=f"Lei {lei_id}")
    
    # 1. Scraping
    console.print()
    with console.status("[bold green]Executando Scraper V2 (Análise Estrutural)...[/bold green]"):
        try:
            scraper = StructuralScraper(path)
            start_time = datetime.now()
            tree_data = scraper.parse()
            duration = (datetime.now() - start_time).total_seconds()
        except Exception as e:
            console.print(f"[red]❌ Erro no Scraper: {e}[/red]")
            Prompt.ask("Enter para voltar")
            return

    root_count = len(tree_data['structure'])
    if root_count == 0:
         console.print("[yellow]⚠️  Nenhuma estrutura encontrada. Verifique se o HTML possui atributos node-id.[/yellow]")
         if not Confirm.ask("Deseja continuar mesmo assim?"):
             return

    console.print(f"[green]✅ Scraping concluído em {duration:.1f}s. {root_count} raízes identificadas.[/green]")

    # 2. Conversão
    with console.status("[bold green]Convertendo para formato Supabase (Flattening)...[/bold green]"):
        try:
            final_data = scraper.convert_to_supabase_format(tree_data)
        except Exception as e:
            console.print(f"[red]❌ Erro na conversão: {e}[/red]")
            Prompt.ask("Enter para voltar")
            return

    # Metadata enrichment
    final_data['lei']['id'] = lei_id
    final_data['lei']['nome'] = lei_nome
    
    # Tenta extrair número
    num_match = re.search(r'\d+', lei_id)
    final_data['lei']['numero'] = num_match.group(0) if num_match else ""
    final_data['lei']['data'] = datetime.now().isoformat()


    # 3. Salvar
    out_file = OUTPUT_DIR / f"{lei_id}_v2.json"
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, indent=2, ensure_ascii=False)

    console.print(f"\n[green]💾 JSON salvo: {out_file.name}[/green]")
    
    # Check stats
    artigos = final_data.get('artigos', [])
    vigentes = sum(1 for a in artigos if a.get('vigente'))
    console.print(f"   Total Artigos: [bold]{len(artigos)}[/bold]")
    console.print(f"   Vigentes: [bold]{vigentes}[/bold]")

    if Confirm.ask("\nDeseja importar para o Supabase agora?"):
        do_import(final_data)
    else:
        Prompt.ask("\nEnter para voltar")


def flow_import():
    show_header()
    console.print(Rule("2. Importar JSON para Supabase", style="blue"))

    # Listar JSONs V2
    files = list(OUTPUT_DIR.glob("*_v2.json"))
    if not files:
        console.print("[yellow]Nenhum arquivo *_v2.json encontrado.[/yellow]")
        Prompt.ask("Enter para voltar")
        return

    table = Table(box=box.ROUNDED)
    table.add_column("#", style="cyan")
    table.add_column("Arquivo")
    table.add_column("Tamanho")

    for i, f in enumerate(files, 1):
        size = f.stat().st_size / 1024
        table.add_row(str(i), f.name, f"{size:.1f} KB")

    console.print(table)
    choice = Prompt.ask("[cyan]Escolha o arquivo[/cyan]", default="1")
    
    try:
        idx = int(choice) - 1
        target_file = files[idx]
    except:
        return

    with open(target_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    do_import(data)
    Prompt.ask("\nEnter para voltar")


def do_import(data: Dict):
    lei_id = data['lei']['id']
    artigos = data.get('artigos', [])
    
    console.print(f"\n[bold cyan]Iniciando importação de {lei_id} ({len(artigos)} artigos)...[/bold cyan]")

    try:
        supabase = get_supabase_client()
        
        # 1. Upsert Lei
        lei_record = {
            'id': lei_id,
            'nome': data['lei']['nome'],
            'numero': data['lei']['numero'],
            'ementa': data['lei'].get('ementa', ''),
            'total_artigos': len(artigos),
            'data_publicacao': data['lei'].get('data'),
            'hierarquia': data['lei'].get('estrutura', []) # Map estrutura -> hierarquia
        }
        supabase.table('leis').upsert(lei_record).execute()
        console.print("✅ Tabela 'leis' atualizada.")

        # 2. Upsert Artigos (Batch)
        batch_size = 50
        prepared_artigos = []
        
        # Pre-process numeric order logic
        for art in artigos:
             # Ensure ordem_numerica is present
             art['ordem_numerica'] = get_ordem_numerica(art.get('numero', ''))
             # Ensure lei_id matches
             art['lei_id'] = lei_id 
             prepared_artigos.append(art)

        with Progress(SpinnerColumn(), BarColumn(), TextColumn("{task.completed}/{task.total}"), transient=False) as progress:
            task = progress.add_task("Importando Artigos...", total=len(prepared_artigos))
            
            for i in range(0, len(prepared_artigos), batch_size):
                batch = prepared_artigos[i:i+batch_size]
                try:
                    supabase.table('artigos').upsert(batch).execute()
                    progress.update(task, advance=len(batch))
                except Exception as e:
                    console.print(f"[red]Erro no lote {i}: {e}[/red]")
                    if Confirm.ask("Tentar insert individual?"):
                        for item in batch:
                            try:
                                supabase.table('artigos').upsert(item).execute()
                            except Exception as e2:
                                console.print(f"[red]Falha art {item.get('id')}: {e2}[/red]")

        console.print(f"[bold green]🚀 Importação de {lei_id} concluída![/bold green]")

    except Exception as e:
        console.print(f"[bold red]❌ Falha Crítica: {e}[/bold red]")


def flow_list():
    show_header()
    console.print(Rule("3. Listar Leis no Supabase", style="yellow"))
    
    try:
        with console.status("Consultando banco..."):
            supabase = get_supabase_client()
            res = supabase.table('leis').select('id, nome, total_artigos').execute()
            
        if not res.data:
            console.print("Nenhuma lei encontrada.")
        else:
            table = Table(box=box.ROUNDED)
            table.add_column("ID", style="cyan")
            table.add_column("Nome")
            table.add_column("Artigos", justify="right")
            
            for l in res.data:
                table.add_row(l['id'], l['nome'], str(l['total_artigos']))
            console.print(table)
            
    except Exception as e:
        console.print(f"[red]Erro: {e}[/red]")
    
    Prompt.ask("\nEnter para voltar")


def flow_delete():
    show_header()
    console.print(Rule("4. Apagar Lei", style="red"))
    
    try:
        supabase = get_supabase_client()
        with console.status("Carregando lista de leis..."):
             res = supabase.table('leis').select('id, nome, numero, total_artigos').execute()
        
        if not res.data:
            console.print("[yellow]Nenhuma lei encontrada para apagar.[/yellow]")
            Prompt.ask("\nEnter para voltar")
            return

        # Sort by nome
        leis = sorted(res.data, key=lambda x: x.get('nome') or "")

        table = Table(box=box.ROUNDED)
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Lei", style="white")
        table.add_column("ID", style="dim")
        
        for i, lei in enumerate(leis, 1):
             table.add_row(str(i), f"{lei['nome']} ({lei['numero']})", lei['id'])
        
        console.print(table)
        console.print("[dim]Digite 0 para cancelar[/dim]")
        
        choice = Prompt.ask("[red]Qual lei deseja apagar? (Número)[/red]")
        
        try:
            idx = int(choice)
            if idx == 0: return
            
            if 1 <= idx <= len(leis):
                target_lei = leis[idx-1]
                target_id = target_lei['id']
                target_name = target_lei['nome']
                
                if Confirm.ask(f"[bold red]TEM CERTEZA?[/bold red] Isso apagará '{target_name}' e TODOS os seus artigos."):
                    with console.status(f"Apagando {target_name}..."):
                        # Delete articles first (FK)
                        supabase.table('artigos').delete().eq('lei_id', target_id).execute()
                        # Delete lei
                        supabase.table('leis').delete().eq('id', target_id).execute()
                    console.print(f"[green]Lei '{target_name}' apagada com sucesso![/green]")
            else:
                console.print("[red]Número inválido.[/red]")

        except ValueError:
            console.print("[red]Entrada inválida.[/red]")

    except Exception as e:
        console.print(f"[red]Erro ao apagar: {e}[/red]")
    
    Prompt.ask("\nEnter para voltar")


def main():
    while True:
        show_header()
        table = Table(box=box.ROUNDED, show_header=False)
        table.add_column("Op", style="bold cyan")
        table.add_column("Desc")
        
        table.add_row("1", "Analisar e Converter (HTML -> JSON)")
        table.add_row("2", "Importar JSON para Supabase")
        table.add_row("3", "Listar Leis no Banco")
        table.add_row("4", "Apagar Lei do Banco")
        table.add_row("0", "Sair")
        
        console.print(table)
        opt = Prompt.ask("\nEscolha", choices=["0","1","2","3","4"], default="1")
        
        if opt == "0": break
        elif opt == "1": flow_analyze()
        elif opt == "2": flow_import()
        elif opt == "3": flow_list()
        elif opt == "4": flow_delete()

if __name__ == "__main__":
    main()
