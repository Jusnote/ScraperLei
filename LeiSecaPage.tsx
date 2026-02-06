'use client';

import { useState, useEffect, useMemo, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { ChevronLeft, ChevronRight, ChevronDown, Search, Star, Flame, History } from "lucide-react";
import dynamic from 'next/dynamic';
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { useLeiContent, useLeis, type ViewMode, type LeiArtigo, type RevokedOnlyMap, buildCapituloPathKey } from "@/hooks/useLeiContent";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from "@/components/ui/resizable";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// Carregar o LeiSecaEditor apenas no client-side
const LeiSecaEditor = dynamic(
  () => import("@/components/lei-seca/lei-seca-editor").then((mod) => ({ default: mod.LeiSecaEditor })),
  { ssr: false }
);

// ============ TIPOS PARA FLAT LIST ============

interface FlatItem {
  id: string;
  type: 'parte' | 'livro' | 'titulo' | 'subtitulo' | 'capitulo' | 'secao' | 'subsecao' | 'artigo';
  level: number;
  name: string;
  artigo?: LeiArtigo;
  artigoIndex?: number;
  parentIds: string[]; // IDs de todos os pais (para determinar visibilidade)
  hasChildren: boolean;
  revokedOnlyItems?: LeiArtigo[];
}

// ============ FLATTEN TREE ============

function buildFlatList(artigos: LeiArtigo[], revokedOnlyMap: RevokedOnlyMap): FlatItem[] {
  const items: FlatItem[] = [];

  // Maps para rastrear headers já adicionados
  const addedHeaders = new Map<string, string>(); // key -> id
  const headerItemsById = new Map<string, FlatItem>();

  // Contadores para gerar IDs únicos
  let idCounter = 0;
  const generateId = () => `item-${idCounter++}`;

  // Helper para adicionar header se não existir
  const ensureHeader = (
    type: FlatItem['type'],
    name: string | null,
    level: number,
    parentIds: string[],
    key: string
  ): string | null => {
    if (!name) return null;

    if (!addedHeaders.has(key)) {
      const id = generateId();
      addedHeaders.set(key, id);
      const headerItem: FlatItem = {
        id,
        type,
        level,
        name,
        parentIds,
        hasChildren: true
      };
      items.push(headerItem);
      headerItemsById.set(id, headerItem);
    }
    return addedHeaders.get(key)!;
  };

  artigos.forEach((artigo, index) => {
    const { parte, livro, titulo, subtitulo, capitulo, secao, subsecao } = artigo.path;
    const parentIds: string[] = [];

    // Adicionar headers na ordem hierárquica (PARTE primeiro, depois LIVRO)
    const parteId = ensureHeader('parte', parte, 0, [], `parte:${parte}`);
    if (parteId) parentIds.push(parteId);

    const livroKey = `livro:${parte || ''}_${livro}`;
    const livroId = ensureHeader('livro', livro, parte ? 1 : 0, [...parentIds], livroKey);
    if (livroId) parentIds.push(livroId);

    const tituloKey = `titulo:${parte || ''}_${livro || ''}_${titulo}`;
    const tituloId = ensureHeader('titulo', titulo, parentIds.length, [...parentIds], tituloKey);
    if (tituloId) parentIds.push(tituloId);

    const subtituloKey = `subtitulo:${parte || ''}_${livro || ''}_${titulo || ''}_${subtitulo}`;
    const subtituloId = ensureHeader('subtitulo', subtitulo, parentIds.length, [...parentIds], subtituloKey);
    if (subtituloId) parentIds.push(subtituloId);

    const capituloKey = buildCapituloPathKey({
      parte,
      livro,
      titulo,
      subtitulo,
      capitulo,
      secao,
      subsecao
    });
    const capituloId = ensureHeader('capitulo', capitulo, parentIds.length, [...parentIds], capituloKey);
    if (capituloId) {
      parentIds.push(capituloId);
      const revokedOnly = revokedOnlyMap[capituloKey];
      if (revokedOnly?.length) {
        const header = headerItemsById.get(capituloId);
        if (header) {
          header.revokedOnlyItems = revokedOnly;
        }
      }
    }

    const secaoKey = `secao:${parte || ''}_${livro || ''}_${titulo || ''}_${subtitulo || ''}_${capitulo || ''}_${secao}`;
    const secaoId = ensureHeader('secao', secao, parentIds.length, [...parentIds], secaoKey);
    if (secaoId) parentIds.push(secaoId);

    const subsecaoKey = `subsecao:${parte || ''}_${livro || ''}_${titulo || ''}_${subtitulo || ''}_${capitulo || ''}_${secao || ''}_${subsecao}`;
    const subsecaoId = ensureHeader('subsecao', subsecao, parentIds.length, [...parentIds], subsecaoKey);
    if (subsecaoId) parentIds.push(subsecaoId);

    // Adicionar artigo
    items.push({
      id: `artigo-${index}`,
      type: 'artigo',
      level: parentIds.length,
      name: `Art. ${artigo.numero}`,
      artigo,
      artigoIndex: index,
      parentIds,
      hasChildren: false
    });
  });

  return items;
}

// ============ COMPONENTE: LINHAS DE HIERARQUIA ============

function TreeLines({ level }: { level: number }) {
  if (level === 0) return null;

  return (
    <div className="flex shrink-0">
      {Array.from({ length: level }).map((_, i) => (
        <div
          key={i}
          className="w-3 h-full flex justify-center"
        >
          <div className="h-full border-l border-dashed border-blue-300 dark:border-blue-700" />
        </div>
      ))}
    </div>
  );
}

// ============ HELPER: PARSE HEADER NAME ============

function parseHeaderName(name: string): { badge: string; description: string } {
  // Patterns para extrair tipo+número da descrição
  // Ex: "TÍTULO I DAS PESSOAS NATURAIS" → badge: "TÍTULO I", description: "Das Pessoas Naturais"
  const patterns = [
    // Padrões com traço (ex: "Livro I - DAS PESSOAS")
    /^(PARTE\s+\w+)$/i,  // PARTE GERAL, PARTE ESPECIAL (sem descrição)
    /^(LIVRO\s+[IVXLCDM\d]+(?:-[A-Z])?)\s*-\s*(.+)$/i,
    /^(TÍTUL?O\s+(?:[IVXLCDM\d]+|único)(?:-[A-Z])?)\s*-\s*(.+)$/i,
    /^(SUBTÍTUL?O\s+[IVXLCDM\d]+(?:-[A-Z])?)\s*-\s*(.+)$/i,
    /^(CAPÍTUL?O\s+(?:[IVXLCDM\d]+|único)(?:-[A-Z])?)\s*-\s*(.+)$/i,
    /^(Se[çc][ãa]o\s+(?:[IVXLCDM\d]+|única?)(?:-[A-Z])?)\s*-\s*(.+)$/i,
    /^(Subse[çc][ãa]o\s+[IVXLCDM\d]+(?:-[A-Z])?)\s*-\s*(.+)$/i,
    // Padrões sem traço (fallback)
    /^(LIVRO\s+[IVXLCDM\d]+(?:-[A-Z])?)\s+(.+)$/i,
    /^(TÍTUL?O\s+[IVXLCDM\d]+(?:-[A-Z])?)\s+(.+)$/i,
    /^(SUBTÍTUL?O\s+[IVXLCDM\d]+(?:-[A-Z])?)\s+(.+)$/i,
    /^(CAPÍTUL?O\s+[IVXLCDM\d]+(?:-[A-Z])?)\s+(.+)$/i,
    /^(Se[çc][ãa]o\s+[IVXLCDM\d]+(?:-[A-Z])?)\s+(.+)$/i,
    /^(Subse[çc][ãa]o\s+[IVXLCDM\d]+(?:-[A-Z])?)\s+(.+)$/i,
  ];

  for (const pattern of patterns) {
    const match = name.match(pattern);
    if (match) {
      return {
        badge: match[1].toUpperCase(),
        description: match[2]
      };
    }
  }

  // Se não encontrou padrão, retorna tudo como badge
  return { badge: name, description: '' };
}

// ============ COMPONENTE DE HEADER ============

interface HeaderItemProps {
  item: FlatItem;
  isExpanded: boolean;
  onToggle: () => void;
}

function HeaderItem({ item, isExpanded, onToggle }: HeaderItemProps) {
  // Parse do nome para separar badge e descrição
  const { badge, description } = parseHeaderName(item.name);
  const hasRevokedOnly = item.type === 'capitulo' && (item.revokedOnlyItems?.length ?? 0) > 0;
  const [showRevokedModal, setShowRevokedModal] = useState(false);

  return (
    <div className="flex pl-3 pr-4 gap-2">
      <TreeLines level={item.level} />

      <div className="flex flex-1 items-start gap-2">
        <button
          onClick={onToggle}
          className={cn(
            "flex-1 flex flex-col gap-0.5 py-2 px-2 text-left rounded-md transition-colors",
            "hover:bg-accent text-foreground",
            isExpanded && "bg-accent/50"
          )}
        >
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-muted text-muted-foreground uppercase tracking-wide">
              {badge}
            </span>
            <div className="flex-1" />
            <ChevronRight className={cn(
              "h-4 w-4 shrink-0 transition-transform duration-200 text-foreground/60",
              isExpanded && "rotate-90"
            )} />
          </div>

          {description && (
            <p className="text-xs text-foreground/70 line-clamp-2 leading-snug pl-0.5">
              {description}
            </p>
          )}
        </button>

        {hasRevokedOnly && (
          <>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setShowRevokedModal(true);
              }}
              className="text-[10px] font-medium px-2 py-1 rounded border border-border bg-muted text-muted-foreground hover:text-foreground"
            >
              <History size={10} className="inline mr-1" />
              Revogados
            </button>
            {showRevokedModal && (
              <div className="fixed inset-0 z-50 flex items-center justify-center">
                <div
                  className="absolute inset-0 bg-black/40"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowRevokedModal(false);
                  }}
                />
                <div className="relative z-50 w-full max-w-xl bg-background border rounded-lg shadow-xl p-5 space-y-4 mx-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-semibold text-foreground">
                        Dispositivos revogados deste capítulo
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Este capítulo não possui artigos vigentes. Consulte o histórico abaixo.
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-xs"
                      onClick={() => setShowRevokedModal(false)}
                    >
                      Fechar
                    </Button>
                  </div>
                  <div className="space-y-3 max-h-96 overflow-y-auto pr-2">
                    {item.revokedOnlyItems?.map((rev) => (
                      <div key={rev.id} className="border rounded-md px-3 py-2 text-xs space-y-1">
                        <p className="font-semibold text-foreground">
                          Art. {rev.numero} (revogado)
                        </p>
                        {rev.epigrafe && (
                          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                            {rev.epigrafe}
                          </p>
                        )}
                        <p className="text-muted-foreground leading-snug whitespace-pre-line">
                          {rev.texto_plano}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ============ COMPONENTE DE ARTIGO ============

interface ArtigoItemProps {
  item: FlatItem;
  isActive: boolean;
  onSelect: () => void;
  header?: React.ReactNode;
}

function ArtigoItem({ item, isActive, onSelect, header }: ArtigoItemProps) {
  const artigo = item.artigo;

  // Preview do texto - pula hierarquia (antes do " | ") e "Art. Xº "
  let previewText = '';
  if (artigo?.texto_plano) {
    let text = artigo.texto_plano;
    const pipeIndex = text.indexOf(' | ');
    if (pipeIndex > -1) text = text.substring(pipeIndex + 3);
    text = text.replace(/^Art\.\s*\d+[º°]?\s*/, '');
    previewText = text.substring(0, 80) + (text.length > 80 ? '...' : '');
  }

  // Mock de dados de prova
  const mockProvas = artigo ? Math.floor((parseInt(artigo.numero) * 7) % 150) : 0;
  const isFavorite = artigo ? parseInt(artigo.numero) % 5 === 0 : false;
  const isRevogado = artigo ? !artigo.vigente : false;
  const revokedContent = artigo?.revoked_versions ?? [];
  const showRevokedTooltip = Boolean(artigo?.vigente && revokedContent.length > 0);

  return (
    <div className="flex py-0.5 pl-3 pr-6">
      {/* Linhas de hierarquia - Esticam para cobrir Header + Card */}
      <TreeLines level={item.level} />

      <div className="flex-1 flex flex-col ml-2">
        {/* Header (Epígrafe) */}
        {header}

        {/* Card do artigo */}
        <div
          onClick={onSelect}
          className={cn(
            "flex-1 group relative py-1.5 px-2.5 rounded-lg border cursor-pointer transition-all",
            isActive
              ? "bg-blue-50 border-blue-200 ring-1 ring-blue-300 dark:bg-blue-950 dark:border-blue-800"
              : "bg-card border-border hover:border-muted-foreground/30 hover:shadow-sm",
            isRevogado && "opacity-60"
          )}
        >
          {/* Barra lateral de frequência (heatmap) */}
          {mockProvas > 80 && (
            <div className="absolute left-0 top-2 bottom-2 w-1 rounded-r-full bg-red-500" />
          )}
          {mockProvas > 40 && mockProvas <= 80 && (
            <div className="absolute left-0 top-2 bottom-2 w-1 rounded-r-full bg-orange-400" />
          )}

          {/* Header: Número + Badge Provas + Favorito */}
          <div className="flex items-center gap-2 pl-1.5">
            <span className={cn(
              "font-bold text-sm shrink-0",
              isActive ? "text-blue-800 dark:text-blue-200" : "text-foreground",
              isRevogado && "line-through"
            )}>
              Art. {artigo?.numero}º
            </span>

            {/* Badge de frequência em provas */}
            {mockProvas > 0 && (
              <div className={cn(
                "flex items-center gap-0.5 text-[10px] font-medium px-1.5 py-0.5 rounded shrink-0",
                mockProvas > 80
                  ? "text-red-600 bg-red-50 dark:text-red-400 dark:bg-red-950"
                  : mockProvas > 40
                    ? "text-orange-600 bg-orange-50 dark:text-orange-400 dark:bg-orange-950"
                    : "text-muted-foreground bg-muted"
              )}>
                <Flame size={9} />
                {mockProvas}x
              </div>
            )}

            {showRevokedTooltip && (
              <TooltipProvider delayDuration={150}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={(e) => e.stopPropagation()}
                      className="flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded bg-muted text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <History size={10} className="mr-1" />
                      Ver revogado
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="right" className="max-w-sm space-y-2 p-3">
                    {revokedContent.map((revArtigo) => (
                      <div key={revArtigo.id} className="text-xs space-y-1">
                        <p className="font-semibold text-foreground">
                          Art. {revArtigo.numero} (revogado)
                        </p>
                        {revArtigo.epigrafe && (
                          <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                            {revArtigo.epigrafe}
                          </p>
                        )}
                        <p className="text-muted-foreground leading-snug">
                          {revArtigo.texto_plano}
                        </p>
                      </div>
                    ))}
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}

            {/* Badge Revogado */}
            {isRevogado && (
              <span className="text-[9px] font-medium text-red-600 bg-red-100 px-1.5 py-0.5 rounded dark:text-red-400 dark:bg-red-950 shrink-0">
                Revogado
              </span>
            )}

            {/* Spacer + Estrela de favorito */}
            <div className="flex-1" />
            <Star
              size={12}
              className={cn(
                "transition-colors shrink-0",
                isFavorite
                  ? "fill-yellow-400 text-yellow-400"
                  : "text-muted-foreground/30 opacity-0 group-hover:opacity-100"
              )}
            />
          </div>

          {/* Preview do Texto - apenas 1 linha */}
          {previewText && (
            <p className={cn(
              "text-xs text-muted-foreground line-clamp-1 pl-1.5 mt-1 leading-snug",
              isRevogado && "line-through"
            )}>
              {previewText}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

// ============ COMPONENTE PRINCIPAL ============

export default function LeiSecaPage() {
  const { leiId: paramLeiId, slug: paramSlug } = useParams<{ leiId?: string; slug?: string }>();
  const navigate = useNavigate();

  // Lista de leis disponíveis
  const { leis, isLoading: leisLoading } = useLeis();

  // Lei atual (default: cc-2002)
  const currentLeiId = paramLeiId || 'cc-2002';

  const [currentArtigoIndex, setCurrentArtigoIndex] = useState(0);
  const [viewMode, setViewMode] = useState<ViewMode>(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());

  // Carrega dados da lei do JSON local
  const {
    lei,
    artigos,
    plateContent,
    isLoading,
    error,
    totalArtigos,
    hasNext,
    hasPrev,
    findArtigoBySlug,
    allArtigos,
    revokedOnlyMap
  } = useLeiContent({
    leiId: currentLeiId,
    viewMode,
    currentArtigoIndex
  });

  // Construir lista flat
  const flatList = useMemo(() => {
    if (!allArtigos || allArtigos.length === 0) return [];
    return buildFlatList(allArtigos, revokedOnlyMap);
  }, [allArtigos, revokedOnlyMap]);

  // Inicializar seções expandidas (primeiro nível)
  useEffect(() => {
    if (flatList.length > 0 && expandedSections.size === 0) {
      const firstLevelHeaders = flatList
        .filter(item => item.type !== 'artigo' && item.level === 0)
        .map(item => item.id);
      setExpandedSections(new Set(firstLevelHeaders));
    }
  }, [flatList]);

  // Normaliza query de busca (remove pontos de milhar para comparar números)
  const normalizeSearchQuery = (query: string) => {
    // Se parece com número, remove pontos para comparar
    const normalized = query.replace(/\./g, '');
    return { original: query.toLowerCase(), normalized };
  };

  // Filtrar itens visíveis baseado nas seções expandidas e busca
  const visibleItems = useMemo(() => {
    return flatList.filter(item => {
      // Verificar se todos os pais estão expandidos
      const allParentsExpanded = item.parentIds.every(parentId => expandedSections.has(parentId));
      if (!allParentsExpanded) return false;

      // Filtro de busca
      if (searchQuery.trim() !== '') {
        const { original, normalized } = normalizeSearchQuery(searchQuery);
        if (item.type === 'artigo') {
          const numeroNormalized = (item.artigo?.numero || '').replace(/\./g, '');
          return item.name.toLowerCase().includes(original) ||
            (item.artigo?.numero?.toString() || '').includes(original) ||
            numeroNormalized.includes(normalized) ||
            (item.artigo?.texto_plano?.toLowerCase() || '').includes(original);
        }
        // Headers: mostrar se match ou se algum filho match
        // Para simplificar, mostrar todos os headers quando há busca
        return item.name.toLowerCase().includes(original);
      }

      return true;
    });
  }, [flatList, expandedSections, searchQuery]);

  // Toggle seção expandida
  const toggleSection = useCallback((sectionId: string) => {
    setExpandedSections(prev => {
      const next = new Set(prev);
      if (next.has(sectionId)) {
        next.delete(sectionId);
      } else {
        next.add(sectionId);
      }
      return next;
    });
  }, []);

  // Expandir todos os pais de um item (para busca)
  useEffect(() => {
    if (searchQuery.trim() !== '') {
      // Quando há busca, expandir todas as seções que contêm matches
      const sectionsToExpand = new Set<string>();
      const { original, normalized } = normalizeSearchQuery(searchQuery);

      flatList.forEach(item => {
        if (item.type === 'artigo') {
          const numeroNormalized = (item.artigo?.numero || '').replace(/\./g, '');
          const matches = item.name.toLowerCase().includes(original) ||
            (item.artigo?.numero?.toString() || '').includes(original) ||
            numeroNormalized.includes(normalized) ||
            (item.artigo?.texto_plano?.toLowerCase() || '').includes(original);
          if (matches) {
            item.parentIds.forEach(id => sectionsToExpand.add(id));
          }
        }
      });
      if (sectionsToExpand.size > 0) {
        setExpandedSections(prev => {
          const next = new Set(prev);
          sectionsToExpand.forEach(id => next.add(id));
          return next;
        });
      }
    }
  }, [searchQuery, flatList]);

  // Navegar para artigo pelo slug na URL
  useEffect(() => {
    if (paramSlug && allArtigos.length > 0) {
      const found = findArtigoBySlug(paramSlug);
      if (found) {
        setCurrentArtigoIndex(found.index);
      }
    }
  }, [paramSlug, allArtigos]);

  // Atualiza URL quando muda o artigo
  const navigateToArtigo = (index: number) => {
    setCurrentArtigoIndex(index);
    const artigo = allArtigos[index];
    if (artigo) {
      const artigoSlug = artigo.slug.replace(`${currentLeiId}-`, '');
      navigate(`/lei-seca/${currentLeiId}/${artigoSlug}`, { replace: true });
    }
  };

  // Troca de lei
  const handleLeiChange = (newLeiId: string) => {
    setCurrentArtigoIndex(0);
    setExpandedSections(new Set());
    navigate(`/lei-seca/${newLeiId}`);
  };

  const handlePrevious = () => {
    if (hasPrev) {
      const step = viewMode === 'full' ? 1 : viewMode;
      const newIndex = Math.max(0, currentArtigoIndex - step);
      navigateToArtigo(newIndex);
    }
  };

  const handleNext = () => {
    if (hasNext) {
      const step = viewMode === 'full' ? 1 : viewMode;
      const newIndex = currentArtigoIndex + step;
      navigateToArtigo(newIndex);
    }
  };

  // Lei selecionada no dropdown
  const currentLeiInfo = leis.find(l => l.id === currentLeiId);
  const currentArtigo = artigos[0];

  if (isLoading) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-neutral-50">
        <p className="text-neutral-600">Carregando lei...</p>
      </div>
    );
  }

  if (error || !lei) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-neutral-50">
        <p className="text-red-600">Erro ao carregar lei: {error?.message}</p>
      </div>
    );
  }

  return (
    <div className="h-full w-full px-60 py-6">
      <ResizablePanelGroup
        direction="horizontal"
        className="h-full w-full rounded-xl border bg-background shadow-sm overflow-hidden"
      >
        {/* ========== PAINEL 1: NAVEGAÇÃO / HIERARQUIA ========== */}
        <ResizablePanel defaultSize={22} minSize={15} maxSize={35}>
          <div className="h-full flex flex-col bg-background border-r">
            {/* Header - Seletor de Lei */}
            <div className="p-4 border-b">
              {/* Dropdown de seleção de lei */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button className="w-full flex items-center justify-between px-3 py-2 text-left bg-accent hover:bg-accent/80 rounded-lg transition-colors">
                    <div className="min-w-0 flex-1">
                      <h2 className="text-base font-semibold text-foreground truncate">
                        {currentLeiInfo?.sigla || lei.nome || lei.numero}
                      </h2>
                      <p className="text-xs text-foreground/70 truncate">
                        {currentLeiInfo?.nome || lei.ementa}
                      </p>
                    </div>
                    <ChevronDown className="h-4 w-4 text-foreground/70 flex-shrink-0 ml-2" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-72">
                  {leis.map((leiItem) => (
                    <DropdownMenuItem
                      key={leiItem.id}
                      onClick={() => handleLeiChange(leiItem.id)}
                      className={cn(
                        "flex flex-col items-start py-2",
                        leiItem.id === currentLeiId && "bg-accent"
                      )}
                    >
                      <span className="font-medium">{leiItem.sigla} - {leiItem.nome}</span>
                      <span className="text-xs text-muted-foreground">{leiItem.total_artigos} artigos</span>
                    </DropdownMenuItem>
                  ))}
                  {leis.length === 0 && !leisLoading && (
                    <DropdownMenuItem disabled>Nenhuma lei disponível</DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>

              <p className="text-xs text-foreground/60 mt-2 line-clamp-2">{lei.ementa}</p>

              {/* Modo de visualização */}
              <div className="flex gap-1 mt-3">
                <Button
                  size="sm"
                  variant={viewMode === 1 ? "default" : "outline"}
                  onClick={() => setViewMode(1)}
                  className="flex-1 h-8"
                >
                  1
                </Button>
                <Button
                  size="sm"
                  variant={viewMode === 5 ? "default" : "outline"}
                  onClick={() => setViewMode(5)}
                  className="flex-1 h-8"
                >
                  5
                </Button>
                <Button
                  size="sm"
                  variant={viewMode === 10 ? "default" : "outline"}
                  onClick={() => setViewMode(10)}
                  className="flex-1 h-8"
                >
                  10
                </Button>
                <Button
                  size="sm"
                  variant={viewMode === 'full' ? "default" : "outline"}
                  onClick={() => setViewMode('full')}
                  className="flex-1 h-8"
                >
                  All
                </Button>
              </div>

              {/* Busca */}
              <div className="relative mt-3">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-foreground/50" />
                <Input
                  placeholder="Buscar artigo..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9 bg-accent border-border h-9"
                />
              </div>
            </div>

            {/* Conteúdo - Lista Flat */}
            <div className="flex-1 overflow-y-auto">
              <div className="flex flex-col pb-4">
                {visibleItems.map((item) => {
                  if (item.type === 'artigo') {
                    // Epígrafe Header (se existir)
                    const epigrafe = item.artigo?.epigrafe;

                    return (
                      <div key={item.id} className="flex flex-col relative">
                        {/* Linhas de hierarquia para o Header também se alinhar */}
                        {epigrafe && (
                          <div className="flex pl-3 pr-6 mb-1 mt-1.5">
                            <TreeLines level={item.level} />
                            <div className="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground/80 pl-2.5 truncate max-w-[220px]" title={epigrafe}>
                              {epigrafe}
                            </div>
                          </div>
                        )}
                        <ArtigoItem
                          item={item}
                          isActive={item.artigoIndex === currentArtigoIndex}
                          onSelect={() => item.artigoIndex !== undefined && navigateToArtigo(item.artigoIndex)}
                        />
                      </div>
                    );
                  }
                  return (
                    <HeaderItem
                      key={item.id}
                      item={item}
                      isExpanded={expandedSections.has(item.id)}
                      onToggle={() => toggleSection(item.id)}
                    />
                  );
                })}
              </div>
            </div>

            {/* Footer - Progresso */}
            <div className="p-4 border-t">
              <div className="text-xs text-foreground/70 space-y-1">
                <div className="flex justify-between">
                  <span>Total de artigos</span>
                  <span className="font-medium text-foreground">{totalArtigos}</span>
                </div>
                <div className="flex justify-between">
                  <span>Visualizando</span>
                  <span className="font-medium text-foreground">
                    {currentArtigoIndex + 1} - {Math.min(currentArtigoIndex + (viewMode === 'full' ? totalArtigos : viewMode), totalArtigos)}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </ResizablePanel>

        <ResizableHandle withHandle />

        {/* ========== PAINEL 2: EDITOR PLATE ========== */}
        <ResizablePanel defaultSize={55} minSize={30}>
          <div className="h-full flex flex-col bg-background">
            {/* Navegação entre artigos */}
            <div className="border-b">
              <div className="px-8 py-4 flex items-center justify-between">
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground hover:text-foreground"
                  onClick={handlePrevious}
                  disabled={!hasPrev}
                >
                  <ChevronLeft className="h-4 w-4 mr-1" />
                  Anterior
                </Button>

                <div className="text-center">
                  <h1 className="text-2xl font-semibold text-foreground">
                    {currentArtigo ? `Art. ${currentArtigo.numero}` : 'Carregando...'}
                  </h1>
                  {currentArtigo?.contexto && (
                    <p className="text-sm text-muted-foreground mt-0.5">
                      {currentArtigo.contexto}
                    </p>
                  )}
                </div>

                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground hover:text-foreground"
                  onClick={handleNext}
                  disabled={!hasNext}
                >
                  Próximo
                  <ChevronRight className="h-4 w-4 ml-1" />
                </Button>
              </div>
            </div>

            {/* Editor */}
            <div className="flex-1 overflow-auto px-4 py-8">
              <div className="max-w-4xl mx-auto">
                <LeiSecaEditor content={plateContent} readOnly={true} />
              </div>
            </div>
          </div>
        </ResizablePanel>

        <ResizableHandle withHandle />

        {/* ========== PAINEL 3: INFORMAÇÕES DO ARTIGO ========== */}
        <ResizablePanel defaultSize={23} minSize={15} maxSize={35}>
          <div className="h-full flex flex-col bg-background border-l">
            <div className="p-6">
              <h3 className="text-sm font-semibold text-foreground mb-4">Informações do Artigo</h3>

              <div className="space-y-4">
                <div className="p-3 rounded-lg bg-muted">
                  <div className="text-xs text-muted-foreground mb-1">Artigos exibidos</div>
                  <div className="text-2xl font-semibold text-foreground">{artigos.length}</div>
                </div>

                <div className="p-3 rounded-lg bg-muted">
                  <div className="text-xs text-muted-foreground mb-1">Modo de visualização</div>
                  <div className="text-lg font-semibold text-foreground">
                    {viewMode === 'full' ? 'Todos' : `${viewMode} por vez`}
                  </div>
                </div>

                <div className="p-3 rounded-lg bg-muted">
                  <div className="text-xs text-muted-foreground mb-1">Contexto</div>
                  <div className="text-sm text-foreground">
                    {currentArtigo?.path.livro || 'N/A'}
                  </div>
                </div>

                {currentArtigo?.path.titulo && (
                  <div className="p-3 rounded-lg bg-muted">
                    <div className="text-xs text-muted-foreground mb-1">Título</div>
                    <div className="text-sm text-foreground">
                      {currentArtigo.path.titulo}
                    </div>
                  </div>
                )}

                {currentArtigo?.path.capitulo && (
                  <div className="p-3 rounded-lg bg-muted">
                    <div className="text-xs text-muted-foreground mb-1">Capítulo</div>
                    <div className="text-sm text-foreground">
                      {currentArtigo.path.capitulo}
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="flex-1 p-6 pt-0">
              <h3 className="text-sm font-semibold text-foreground mb-3">Notas</h3>
              <p className="text-sm text-muted-foreground">Funcionalidade em desenvolvimento</p>
            </div>
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
}
