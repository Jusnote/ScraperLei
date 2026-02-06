import json
import re
from bs4 import BeautifulSoup
from typing import Dict, List, Optional, Any

class StructuralScraper:
    # Regex para capturar anotações no FINAL do texto
    # Usa padrões que funcionam com ou sem acentos
    # Padrão: palavra de ação legislativa + "pel[ao]" em qualquer posição dentro do parêntese
    RE_ANOTACOES_FINAL = re.compile(
        r'(\s*\((?=[^)]*(?:(?:inclu[íi]d|revogad|acrescid|alterad|vetad|suprimi|renumerad)[oa]?.*pel[ao]|reda[çc][ãa]o\s+dad|vide|vig[êe]ncia))[^)]+\))+$',
        re.IGNORECASE
    )

    # Regex para separar anotações individuais
    RE_ANOTACAO_INDIVIDUAL = re.compile(r'\([^)]+\)')

    def __init__(self, html_path: str):
        self.html_path = html_path
        self.soup = None
        self.root_nodes = []

    def _separar_anotacoes(self, texto: str) -> tuple:
        """
        Separa anotações legislativas do texto.
        Returns: (texto_limpo, texto_original, anotacoes[])
        """
        if not texto:
            return '', '', []

        match = self.RE_ANOTACOES_FINAL.search(texto)
        if not match:
            return texto, texto, []

        bloco_anotacoes = match.group()
        texto_limpo = texto[:match.start()].strip()
        anotacoes = [a.strip() for a in self.RE_ANOTACAO_INDIVIDUAL.findall(bloco_anotacoes)]

        return texto_limpo, texto, anotacoes

    def _formatar_label_artigo(self, numero: str) -> str:
        """
        Formata o label do artigo conforme regras de redação legislativa.
        - Art. 1º a 9º: ordinal com símbolo
        - Art. 10 em diante: cardinal sem símbolo
        """
        match = re.match(r'^(\d+)(-[A-Za-z])?$', numero)
        if not match:
            return f"Art. {numero}"

        num_base = int(match.group(1))
        sufixo = match.group(2) or ""

        if num_base <= 9:
            return f"Art. {num_base}º{sufixo}"
        else:
            return f"Art. {num_base}{sufixo}"

    def _formatar_label_paragrafo(self, numero: str) -> str:
        """
        Formata o label do parágrafo conforme regras de redação legislativa.
        - § 1º a § 9º: ordinal com símbolo
        - § 10 em diante: cardinal sem símbolo
        - Suporta sufixos como -A, -B
        """
        if numero == 'unico':
            return "Parágrafo único"

        match = re.match(r'^(\d+)(-.+)?$', numero)
        if match:
            base = int(match.group(1))
            suffix = match.group(2) or ''
            return f"§ {base}º{suffix}" if base <= 9 else f"§ {base}{suffix}"
        return f"§ {numero}"

    def _verificar_revogado_vetado(self, texto_limpo: str, anotacoes: list) -> tuple:
        """
        Verifica se um dispositivo está revogado ou vetado baseado nas anotações.
        Returns: (is_revogado, is_vetado)
        """
        is_revogado = False
        is_vetado = False

        # Remove pontuação solitária para verificar se está "vazio"
        texto_sem_pontuacao = re.sub(r'^[\s\.\,\;\:\-]+$', '', texto_limpo.strip())

        if not texto_sem_pontuacao:
            anotacoes_lower = ' '.join(anotacoes).lower() if anotacoes else ''

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

        return is_revogado, is_vetado

    def load_html(self):
        """Loads and parses the HTML file."""
        with open(self.html_path, 'r', encoding='utf-8') as f:
            self.soup = BeautifulSoup(f, 'html.parser')
        print(f"Loaded HTML from {self.html_path}")

    def parse(self) -> Dict[str, Any]:
        """Main parsing method. Returns the structural JSON tree."""
        if not self.soup:
            self.load_html()

        # Find the main container - based on observation, it's often a div with class 'meteredContent'
        # or simply we look for top-level nodes. The provided snippet shows:
        # <div id="law_content_container" class="meteredContent">
        container = self.soup.find(id="law_content_container")
        if not container:
            container = self.soup.find(class_="meteredContent")
        
        if not container:
            raise ValueError("Could not find law content container (id='law_content_container' or class='meteredContent')")

        # The structure is flat in HTML but hierarchical by ID.
        # Actually, looking at the snippet:
        # <div node-id="1"> ... </div>
        #   <div node-id="1.0"> ... </div>
        # They are SIBLINGS in the HTML structure, not nested <div>s.
        # This is a critical insight. We must reconstruct the tree from the node-ids.

        nodes = []
        # Find all divs with a node-id
        all_divs = container.find_all("div", attrs={"node-id": True})
        
        for div in all_divs:
            node_data = self._extract_node_data(div)
            nodes.append(node_data)

        # Build tree from flat list of nodes based on node-id
        tree = self._build_tree(nodes)
        
        return {
            "metadata": {"source": self.html_path, "type": "structural_v2"},
            "structure": tree
        }

    def _extract_node_data(self, div) -> Dict[str, Any]:
        """Extracts raw data from a single HTML node."""
        node_id = div.get("node-id")
        semantic_id = div.get("semantic-id", "")
        
        # Extract Text
        # Usually inside a header tag (h3, h4, h5) or p tag.
        # Snippets:
        # Artigo 1: <h5>Anterioridade da Lei</h5> <h4 class="styles_title__4pWyM">Art. 1º</h4> <p class="highlight_content">...</p>
        
        text_content = ""
        # Get all textual content except standard UI buttons (remissions often have buttons)
        
        # Strategy: Get text from headers and paragraphs
        texts = []
        
        # Epigraph/Rubrica (e.g. "Anterioridade da Lei")
        epigraph_tag = div.find(["h5", "h3"]) # h3 is sometimes Part/Title header
        epigraph = epigraph_tag.get_text(strip=True) if epigraph_tag else ""

        # Label (e.g. "Art. 1º", "Título I", "Parte geral")
        label_tag = div.find("h4") or div.find("span", class_=re.compile("styles_title"))
        label = label_tag.get_text(strip=True) if label_tag else ""
        
        # Content (The actual law text)
        p_content = div.find("p", class_="highlight_content")
        content_text = ""
        if p_content:
            # Check for spans that might be remissions triggers and ignore them if needed
            # But usually get_text() is fine, maybe strip buttons?
            # Buttons are usually for copy/share, might be inside? In snippet they are inside <span remissions_triggers>
            # Let's strip buttons
            for btn in p_content.find_all("button"):
                btn.decompose()
            # Preserve semantic newlines for list parsing
            content_text = p_content.get_text("\n", strip=True)

        # Infer type from semantic-id or label
        node_type = self._infer_type(semantic_id, label, node_id)

        node = {
            "id": node_id,
            "semantic_id": semantic_id,
            "type": node_type,
            "epigraph": epigraph,
            "label": label,
            "text": content_text,
            "children": [] # Placeholder
        }
        
        # SPECIAL HANDLING: Inline Items (Lei 9503 style)
        # If text contains "1 - " or "1. " list pattern, parse it into children
        if node_type in ['alinea', 'inciso', 'paragrafo']: # Usually items are inside these
            self._parse_inline_items(node)
            
        return node

    def _parse_inline_items(self, node: Dict[str, Any]):
        """Parses inline text numbers (1 -, 2 -) into virtual Item nodes."""
        text = node['text']
        if not text: return
        
        # Pattern: Newline + Number + Separator + Content
        # We look for a sequence: " 1 - ", " 2 - " ...
        # Regex for capturing:
        # (?:^|\n)\s*(\d+)\s*[-\.]\s+(.*?)(?=\n\s*\d+\s*[-\.]|\Z)
        
        # But first, check if it looks like a list
        # Check for "\n1 -" or "\n1." or start of string "1 -"
        has_list = re.search(r'(?:^|\n)\s*1\s*[-\.]', text)
        
        if not has_list:
            return
            
        items = []
        # Split text by item pattern
        # This is tricky because we want to keep the preamble text in the parent
        
        # Find first match
        first_match = re.search(r'(?:^|\n)\s*1\s*[-\.]', text)
        if not first_match: return

        
        preamble = text[:first_match.start()].strip()
        list_content = text[first_match.start():]
        
        # Update parent text to just the preamble (e.g. "compreendendo:")
        node['text'] = preamble
        
        # Parse items
        # Pattern: \n\s*(\d+)\s*[-\.]\s+
        # We iterate manually to handle multi-line items safely
        
        lines = list_content.split('\n')
        current_item = None
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            # Check if start of new item
            m = re.match(r'^(\d+)\s*[-\.]\s+(.*)', line)
            if m:
                # Save previous
                if current_item: items.append(current_item)
                
                # Start new
                num_str = m.group(1)
                content = m.group(2)
                current_item = {
                    "id": f"{node['id']}.item.{num_str}", # Virtual ID
                    "semantic_id": f"{node.get('semantic_id','')}.item-{num_str}",
                    "type": "item",
                    "epigraph": "",
                    "label": num_str,
                    "text": content,
                    "children": []
                }
            else:
                # Continuation of current item
                if current_item:
                    current_item['text'] += " " + line
        
        if current_item: items.append(current_item)
        
        # Add to children
        node['children'].extend(items)


    def _infer_type(self, semantic_id: str, label: str, node_id: str) -> str:
        s = semantic_id.lower()
        # Order matters! Specific types first.
        # IMPORTANT: .penalty must be checked BEFORE artigo
        if ".penalty" in s: return "penalty"  # Penalty is a child, not a separate article
        if "alinea" in s: return "alinea"
        if "inciso" in s: return "inciso"
        if "item" in s: return "item"
        if "paragrafo" in s: return "paragrafo"
        if "artigo" in s: return "artigo"
        if "secao" in s: return "secao"
        if "capitulo" in s: return "capitulo"
        if "titulo" in s: return "titulo"
        if "livro" in s: return "livro"
        if "parte" in s: return "parte"
        
        # Fallback to label
        l = label.lower()
        if "§" in l or "parágrafo" in l: return "paragrafo"
        if l.startswith("pena"): return "penalty"  # Label fallback for Pena
        if l.startswith("art"): return "artigo"
        if l.startswith("capítulo") or l.startswith("capitulo"): return "capitulo"
        if l.startswith("título") or l.startswith("titulo"): return "titulo"
        if l.startswith("seção") or l.startswith("secao"): return "secao"
        if l.startswith("livro"): return "livro"
        if l.startswith("parte"): return "parte"
        if re.match(r'^[a-z]\s*\)', l): return "alinea" # a) b)
        if re.match(r'^[ivx]+\s*[-\u2013]', l): return "inciso" # I - 
        
        return "unknown"

    def _build_tree(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reconstructs the tree hierarchy from a flat list of nodes with dotted IDs (1.0.1)."""
        # Sort by node-id just in case
        # Node IDs are like "1", "1.0", "1.0.0"
        # We can dict-map them
        
        node_map = {n['id']: n for n in nodes}
        roots = []

        # We assume the list is in traversal order (document order), which is usually true for HTML.
        # But to be safe, we can look for parents.
        
        for node in nodes:
            nid = node['id']
            parts = nid.split('.')
            
            # Find parent ID
            # Logic: verify if removing the last segment gives a valid parent
            # "1.0.0" -> parent "1.0"
            # "1.0" -> parent "1"
            # "1" -> parent None
            
            parent_found = False
            if len(parts) > 1:
                parent_id = ".".join(parts[:-1])
                if parent_id in node_map:
                    node_map[parent_id]['children'].append(node)
                    parent_found = True
            
            if not parent_found:
                roots.append(node)
                
        return roots

    def _build_artigo_payload(self, node, context_path, path_dict):
        import uuid
        import hashlib

        # 1. Flatten children for plate_content
        plate_content = []
        full_text = []

        # Add Epigraph as first element if it exists (for editor rendering)
        epigraph = node.get('epigraph', '')
        if epigraph:
            # Separa anotações da epígrafe
            epigraph_limpa, epigraph_original, epigraph_anotacoes = self._separar_anotacoes(epigraph)
            plate_content.append({
                "type": "p",
                "children": [{"text": epigraph_limpa, "bold": True}],
                "id": str(uuid.uuid4()),
                "slug": f"{node['semantic_id']}_epigrafe" if node.get('semantic_id') else "epigrafe",
                "search_text": epigraph_limpa,
                "texto_original": epigraph_original if epigraph_anotacoes else None,
                "anotacoes": epigraph_anotacoes if epigraph_anotacoes else None
            })

        # Extrai número do artigo
        if node['semantic_id'] and node['semantic_id'].startswith("artigo-"):
            numero = node['semantic_id'].replace("artigo-", "")
        else:
            num_match = re.search(r'(\d+(?:[-]?[a-zA-Z])?)', node['label'])
            numero = num_match.group(1) if num_match else "0"

        # Formata label conforme regras de redação legislativa
        label = self._formatar_label_artigo(numero)
        text_original = node['text'].strip()

        # Separa anotações do caput
        text_limpo, _, anotacoes_caput = self._separar_anotacoes(text_original)

        caput_text_limpo = f"{label} {text_limpo}".strip()
        caput_text_original = f"{label} {text_original}".strip()
        full_text.append(caput_text_limpo)

        # Split label from text for bold formatting
        if label and text_limpo:
            children = [{"text": label + " ", "bold": True}, {"text": text_limpo}]
        else:
            children = [{"text": caput_text_limpo}]

        plate_content.append({
            "type": "p",
            "children": children,
            "id": str(uuid.uuid4()),
            "slug": "caput",
            "search_text": caput_text_limpo,
            "texto_original": caput_text_original if anotacoes_caput else None,
            "anotacoes": anotacoes_caput if anotacoes_caput else None
        })

        # Helper traverse for content children (Par, Inc, Ali, Item)
        def flatten_content(children_nodes, indent=0):
            for child in children_nodes:
                child_type = child.get('type', '')
                child_text_original = child['text'].strip()

                # Separa anotações do filho
                child_text_limpo, _, child_anotacoes = self._separar_anotacoes(child_text_original)

                # Formata label conforme tipo do dispositivo
                if child_type == 'paragrafo':
                    # Extrai número do parágrafo do semantic_id ou label
                    par_numero = ''
                    if child.get('semantic_id') and 'paragrafo-' in child['semantic_id']:
                        par_numero = child['semantic_id'].split('paragrafo-')[-1].split('.')[0]
                    elif '§' in child['label']:
                        par_match = re.search(r'§\s*(\d+[º°]?(?:-[A-Za-z])?)', child['label'])
                        if par_match:
                            par_numero = par_match.group(1).replace('º', '').replace('°', '')
                    child_label = self._formatar_label_paragrafo(par_numero) if par_numero else child['label'].strip()
                else:
                    child_label = child['label'].strip()

                # Verifica se é revogado/vetado
                is_revogado, is_vetado = self._verificar_revogado_vetado(child_text_limpo, child_anotacoes)

                # FIX: Check for nested epigraph (Rubrica) for Alíneas/Incisos
                child_epigraph = child.get('epigraph', '').strip()
                if child_epigraph:
                    # Separa anotações da epígrafe/rubrica
                    epigraph_limpa, epigraph_original, epigraph_anotacoes = self._separar_anotacoes(child_epigraph)
                    # Append Epigraph Block first
                    plate_content.append({
                        "type": "p",
                        "children": [{"text": epigraph_limpa, "bold": True, "italic": True}],
                        "id": str(uuid.uuid4()),
                        "slug": f"{child['semantic_id']}-epigraph" if child.get('semantic_id') else str(uuid.uuid4()),
                        "search_text": epigraph_limpa,
                        "texto_original": epigraph_original if epigraph_anotacoes else None,
                        "anotacoes": epigraph_anotacoes if epigraph_anotacoes else None,
                        "indent": indent + 1
                    })
                    full_text.append(epigraph_limpa)

                # Se revogado ou vetado, substitui texto
                if is_revogado or is_vetado:
                    texto_exibir = "Dispositivo revogado." if is_revogado else "Dispositivo vetado."
                    child_full_text_limpo = f"{child_label} {texto_exibir}".strip()
                    child_full_text_original = f"{child_label} {child_text_original}".strip()
                    full_text.append(child_full_text_limpo)

                    # Bloco plate com estilo tachado e cinza
                    plate_content.append({
                        "type": "p",
                        "children": [
                            {"text": child_label + " ", "bold": True, "strikethrough": True, "color": "#9ca3af"},
                            {"text": texto_exibir, "strikethrough": True, "color": "#9ca3af"}
                        ],
                        "id": str(uuid.uuid4()),
                        "slug": child['semantic_id'] or str(uuid.uuid4()),
                        "search_text": child_full_text_limpo,
                        "texto_original": child_full_text_original if child_anotacoes else None,
                        "anotacoes": child_anotacoes if child_anotacoes else None,
                        "indent": indent + 1,
                        "revogado": is_revogado,
                        "vetado": is_vetado
                    })
                else:
                    child_full_text_limpo = f"{child_label} {child_text_limpo}".strip()
                    child_full_text_original = f"{child_label} {child_text_original}".strip()
                    full_text.append(child_full_text_limpo)

                    # Split label from text for bold formatting
                    if child_label and child_text_limpo:
                        child_children = [{"text": child_label + " ", "bold": True}, {"text": child_text_limpo}]
                    else:
                        child_children = [{"text": child_full_text_limpo}]

                    # Add to plate
                    plate_content.append({
                        "type": "p",
                        "children": child_children,
                        "id": str(uuid.uuid4()),
                        "slug": child['semantic_id'] or str(uuid.uuid4()),
                        "search_text": child_full_text_limpo,
                        "texto_original": child_full_text_original if child_anotacoes else None,
                        "anotacoes": child_anotacoes if child_anotacoes else None,
                        "indent": indent + 1
                    })

                # Recurse (Item is inside Alínea, indent increases)
                flatten_content(child['children'], indent + 1)

        flatten_content(node['children'], indent=0)

        # 2. Metadata (numero já extraído no início da função)
        final_text = "\n".join(full_text)
        content_hash = hashlib.md5(final_text.encode('utf-8')).hexdigest()
        
        context_str = " > ".join(context_path)
        
        # Fix Validity Logic: Check ONLY the caput for revogation/veto status.
        # Do NOT check children (incisos, paragraphs) - they may be revoked individually.
        
        # Normalize caput only (node['label'] + node['text'])
        caput_only = f"{node['label']} {node['text']}".strip().lower()
        is_revogado = False
        
        # Check 1: Caput contains "(revogado)" or "(vetado)"
        if "(revogad" in caput_only or "(vetado" in caput_only:
            is_revogado = True
        # Check 2: Very short caput that is essentially just the label + revoked marker
        elif len(caput_only) < 30 and ("revogad" in caput_only or "vetado" in caput_only):
            is_revogado = True
            
        return {
            "id": node['semantic_id'] or str(uuid.uuid4()),
            "numero": numero,
            "slug": node['semantic_id'],
            "epigrafe": node['epigraph'],
            "plate_content": plate_content,
            "texto_plano": final_text,
            "search_text": final_text,
            "vigente": not is_revogado,
            "contexto": context_str,
            "path": path_dict, 
            "content_hash": content_hash
        }



    def _extract_legacy_hierarchy(self, nodes: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """
        Extracts hierarchy in the legacy flat dictionary format expected by the frontend.
        Format: { "partes": [], "livros": [], "titulos": [], "capitulos": [], "secoes": [], "subsecoes": [] }
        """
        hierarchy = {
            "partes": [],
            "livros": [],
            "titulos": [],
            "capitulos": [], 
            "secoes": [],
            "subsecoes": []
        }
        
        # Map singular node types to plural hierarchy keys
        type_map = {
            "parte": "partes",
            "livro": "livros",
            "titulo": "titulos",
            "capitulo": "capitulos",
            "secao": "secoes",
            "subsecao": "subsecoes"
        }
        
        def traverse(node_list):
            for node in node_list:
                ntype = node['type']
                text = node['text']
                label = node['label']
                full_text = f"{label} - {text}" if text else label
                
                # Use the map to find the correct key
                if ntype in type_map:
                    key = type_map[ntype]
                    if key in hierarchy:
                        hierarchy[key].append(full_text)
                    
                traverse(node['children'])
                
        traverse(nodes)
        
        # Ensure we don't have duplicates if logic traversed same nodes? 
        # Tree is strictly hierarchical so no need to worry about cycles.
        return hierarchy

    def convert_to_supabase_format(self, tree_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts the hierarchical tree into the flat 'plate' format required by Supabase.
        Adds: context strings, paths, indent levels, plate_content JSON.
        """
        import hashlib
        import uuid
        
        flat_artigos = []
        hierarchy_stack = []
        

        
        # Helper to traverse and capture context
        def traverse(nodes, context_path, path_dict):
            for node in nodes:
                ntype = node['type']
                label = node['label']
                text = node['text']
                
                # Context Management
                new_ctx = context_path
                new_path_dict = path_dict.copy()
                
                if ntype in ['parte', 'livro', 'titulo', 'capitulo', 'secao', 'subsecao']:
                     full_title = f"{label} - {text}" if text else label
                     new_ctx = context_path + [full_title]
                     # Update path dict (e.g. "capitulo": "Capítulo I")
                     # Frontend expects "number" or "label"? Usually just the label "Capítulo I"
                     # Looking at valid json (leidecrimes), it has:
                     # "path": { "parte": "Parte geral", "titulo": "Título I - ..." }
                     
                     new_path_dict[ntype] = full_title

                if ntype == 'artigo':
                    # Only create article entry for actual artigos (not penalty nodes)
                    # Penalty nodes are children and will be included in parent's plate_content
                    art_obj = self._build_artigo_payload(node, context_path, new_path_dict)
                    flat_artigos.append(art_obj)
                
                # RECURSION
                traverse(node['children'], new_ctx, new_path_dict)

        traverse(tree_data['structure'], [], {})
        
        # Extract Sidebar Hierarchy (Legacy Format)
        estrutura_sidebar = self._extract_legacy_hierarchy(tree_data['structure'])
        
        return {
            "lei": {
                "id": "", 
                "nome": "", 
                "numero": "", 
                "ementa": "",
                "estrutura": estrutura_sidebar, 
            },
            "artigos": flat_artigos
        }

def main():
    import os
    base_dir = r"C:\Users\Home\Documents\Scraper-Lei"
    
    # Try to find recent files
    target_file = None
    files = [f for f in os.listdir(base_dir) if "html" not in f and ("14133" in f or "9503" in f)]
    if files:
         target_file = os.path.join(base_dir, files[0])
    
    if not target_file:
         print("No suitable test file found.")
         return

    print(f"Targeting: {target_file}")
    scraper = StructuralScraper(target_file)
    data = scraper.parse()
    
    # Save output for user
    out_filename = "resultado_scraper_v2.json"
    out_path = os.path.join(base_dir, out_filename)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Saved JSON output to: {out_filename}")
    
    print("\n--- Summary ---")
    print(f"Total Roots: {len(data['structure'])}")
    
    # Quick Check for deep nodes
    max_depth = 0
    def check_depth(nodes, depth=1):
        nonlocal max_depth
        if depth > max_depth: max_depth = depth
        for n in nodes:
            check_depth(n['children'], depth+1)
            
    check_depth(data['structure'])
    print(f"Max nesting depth found: {max_depth}")
    
    # Check for Items
    item_count = 0
    def count_items(nodes):
        nonlocal item_count
        for n in nodes:
            if n['type'] == 'item': item_count += 1
            count_items(n['children'])
    count_items(data['structure'])
    print(f"Total Items found: {item_count}")
    
    print("\n✅ Scraper V2 is ready.")

if __name__ == "__main__":
    main()
