import { useState, useEffect, useMemo } from 'react';
import { supabase } from '@/integrations/supabase/client';

// ============ CONFIGURAÇÃO ============

// Flag para alternar entre JSON local e Supabase
// true = busca do Supabase | false = busca do JSON local
const USE_SUPABASE = process.env.NEXT_PUBLIC_USE_SUPABASE === 'true';

// ============ TIPOS ============

export interface LeiIndex {
  id: string;
  nome: string;
  sigla: string;
  numero: string;
  data: string;
  ementa: string;
  total_artigos: number;
}

export interface LeisIndex {
  leis: LeiIndex[];
}

export interface LeiArtigo {
  id: string;
  numero: string;
  slug: string;  // Slug canônico para referência cruzada (ex: "cc-2002-art-121-par-2-inc-iv")
  search_text: string;  // Fingerprint de busca: texto limpo sem acentos/pontuação
  vigente: boolean;  // False se revogado/vetado - para filtrar no RAG
  path: {
    parte: string | null;
    livro: string | null;
    titulo: string | null;
    subtitulo: string | null;
    capitulo: string | null;
    secao: string | null;
    subsecao: string | null;
  };
  contexto: string;
  plate_content: any[];  // Cada nó tem: id (uuid), slug, search_text, type, children, indent?
  texto_plano: string;
  epigrafe?: string; // Rubrica/Título do artigo (ex: "Homicídio")
  revoked_versions?: LeiArtigoRevogado[];
}

export interface LeiArtigoRevogado {
  id: string;
  numero: string;
  texto_plano: string;
  plate_content: any[];
  epigrafe?: string;
  contexto?: string;
}

export type RevokedOnlyMap = Record<string, LeiArtigo[]>;

export interface Lei {
  id: string;
  numero: string;
  nome: string;
  data: string;
  ementa: string;
  hierarquia: {
    partes: string[];
    livros: string[];
    titulos: string[];
    subtitulos: string[];
    capitulos: string[];
    secoes: string[];
    subsecoes: string[];
  };
  total_artigos: number;
}

export interface LeiData {
  lei: Lei;
  artigos: LeiArtigo[];
  revokedOnlyMap?: RevokedOnlyMap;
}

export type ViewMode = 1 | 5 | 10 | 'full';

// ============ HELPER: NATURAL SORT ============

function getArtigoSortValue(numero: string): [number, string] {
  // Remove caracteres não numéricos exceto traço e letras para sufixo
  // Ex: "1º" -> "1", "121-A" -> "121-A"

  // Extrair parte numérica principal
  const match = numero.match(/^(\d+)(.*)$/);
  if (!match) return [0, numero];

  const num = parseInt(match[1], 10);
  const suffix = match[2].trim(); // "-A", "º", etc

  // Normalizar sufixo para ordenação
  // "º" deve vir antes de qualquer letra/hífen? 
  // Na verdade: 1 < 1-A. E 1º é tratado como 1.

  return [num, suffix];
}

function sortArtigos(artigos: LeiArtigo[]): LeiArtigo[] {
  return [...artigos].sort((a, b) => {
    const [numA, sufA] = getArtigoSortValue(a.numero);
    const [numB, sufB] = getArtigoSortValue(b.numero);

    if (numA !== numB) {
      return numA - numB;
    }

    return sufA.localeCompare(sufB);
  });
}

export function buildCapituloPathKey(path: LeiArtigo['path']): string {
  return [
    'capitulo',
    path?.parte ?? '',
    path?.livro ?? '',
    path?.titulo ?? '',
    path?.subtitulo ?? '',
    path?.capitulo ?? ''
  ].join('|');
}

function normalizeArtigos(rows: any[]): { artigos: LeiArtigo[]; revokedOnlyMap: RevokedOnlyMap } {
  const revokedIds = new Set<string>();
  rows.forEach((row) => {
    if (row.vigente === false) return;
    const revokedList = (row.revoked_versions as LeiArtigoRevogado[]) || [];
    revokedList.forEach((rev) => {
      if (rev?.id) revokedIds.add(rev.id);
    });
  });

  const revokedOnlyMap: RevokedOnlyMap = {};
  const artigos: LeiArtigo[] = [];

  rows.forEach((row) => {
    const mapped: LeiArtigo = {
      id: row.id,
      numero: row.numero,
      slug: row.slug || '',
      search_text: row.search_text || '',
      vigente: row.vigente ?? true,
      path: (row.path as LeiArtigo['path']) || {
        parte: null, livro: null, titulo: null, subtitulo: null, capitulo: null, secao: null, subsecao: null,
      },
      contexto: row.contexto || '',
      plate_content: (row.plate_content as any[]) || [],
      texto_plano: row.texto_plano || '',
      epigrafe: row.epigrafe || '',
      revoked_versions: (row.revoked_versions as LeiArtigoRevogado[]) || [],
    };

    if (mapped.vigente === false && revokedIds.has(mapped.id)) {
      return;
    }

    if (mapped.vigente === false) {
      const key = buildCapituloPathKey(mapped.path);
      if (!revokedOnlyMap[key]) {
        revokedOnlyMap[key] = [];
      }
      revokedOnlyMap[key].push(mapped);
      return;
    }

    artigos.push(mapped);
  });

  Object.values(revokedOnlyMap).forEach(list => {
    list.sort((a, b) => {
      const [numA, sufA] = getArtigoSortValue(a.numero);
      const [numB, sufB] = getArtigoSortValue(b.numero);
      if (numA !== numB) return numA - numB;
      return sufA.localeCompare(sufB);
    });
  });

  return { artigos: sortArtigos(artigos), revokedOnlyMap };
}

interface UseLeiContentOptions {
  leiId: string;
  viewMode: ViewMode;
  currentArtigoIndex: number;
}

export function useLeiContent({ leiId, viewMode, currentArtigoIndex }: UseLeiContentOptions) {
  const [leiData, setLeiData] = useState<LeiData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  // Carrega dados da lei (Supabase ou JSON local baseado na flag)
  useEffect(() => {
    const loadFromSupabase = async (): Promise<LeiData> => {
      // 1. Buscar dados da lei
      const { data: leiRow, error: leiError } = await supabase
        .from('leis')
        .select('*')
        .eq('id', leiId)
        .single();

      if (leiError || !leiRow) {
        throw new Error(`Lei não encontrada no Supabase: ${leiId}`);
      }

      // 2. Buscar artigos ordenados (limite alto para leis extensas como CC)
      const { data: artigosRows, error: artigosError } = await supabase
        .from('artigos')
        .select('*')
        .eq('lei_id', leiId)
        .order('ordem_numerica', { ascending: true })
        .limit(5000);

      if (artigosError) {
        throw new Error(`Erro ao buscar artigos: ${artigosError.message}`);
      }

      // 3. Montar estrutura LeiData
      const lei: Lei = {
        id: leiRow.id,
        numero: leiRow.numero || '',
        nome: leiRow.nome || '',
        data: leiRow.data_publicacao || '',
        ementa: leiRow.ementa || '',
        hierarquia: (leiRow.hierarquia as Lei['hierarquia']) || {
          partes: [], livros: [], titulos: [], subtitulos: [], capitulos: [], secoes: [], subsecoes: []
        },
        total_artigos: leiRow.total_artigos || 0,
      };

      const { artigos, revokedOnlyMap } = normalizeArtigos(artigosRows || []);
      return { lei, artigos, revokedOnlyMap };
    };

    const loadFromLocalJson = async (): Promise<LeiData> => {
      const response = await fetch(`/data/leis/${leiId}.json`);

      if (!response.ok) {
        throw new Error(`Lei não encontrada: ${leiId}`);
      }

      const json = await response.json();
      const { artigos, revokedOnlyMap } = normalizeArtigos(json.artigos || []);
      return { lei: json.lei, artigos, revokedOnlyMap };
    };

    const loadLei = async () => {
      try {
        setIsLoading(true);

        const data = USE_SUPABASE
          ? await loadFromSupabase()
          : await loadFromLocalJson();

        setLeiData(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err : new Error('Erro ao carregar lei'));
        setLeiData(null);
      } finally {
        setIsLoading(false);
      }
    };

    loadLei();
  }, [leiId]);

  // Calcula artigos a serem exibidos baseado no viewMode
  const artigosExibidos = useMemo(() => {
    if (!leiData) return [];

    if (viewMode === 'full') {
      return leiData.artigos;
    }

    const count = viewMode as number;
    const start = currentArtigoIndex;
    const end = start + count;

    return leiData.artigos.slice(start, end);
  }, [leiData, viewMode, currentArtigoIndex]);

  // Combina plate_content de todos artigos exibidos
  const plateContent = useMemo(() => {
    const combined: any[] = [];

    artigosExibidos.forEach((artigo, index) => {
      // Adiciona conteúdo do artigo (que já contém a epígrafe se injetada pelo conversor)
      combined.push(...artigo.plate_content);

      // Adiciona espaçamento entre artigos (exceto o último)
      if (index < artigosExibidos.length - 1) {
        combined.push({
          type: 'p',
          children: [{ text: '' }]
        });
      }
    });

    return combined;
  }, [artigosExibidos]);

  // Metadados
  const totalArtigos = leiData?.lei.total_artigos ?? 0;
  const hasNext = currentArtigoIndex + (viewMode === 'full' ? totalArtigos : (viewMode as number)) < totalArtigos;
  const hasPrev = currentArtigoIndex > 0;

  return {
    // Dados
    lei: leiData?.lei ?? null,
    artigos: artigosExibidos,
    plateContent,

    // Estado
    isLoading,
    error,

    // Navegação
    totalArtigos,
    currentArtigoIndex,
    hasNext,
    hasPrev,

    // Hierarquia para sidebar
    hierarquia: leiData?.lei.hierarquia ?? { partes: [], livros: [], titulos: [], subtitulos: [], capitulos: [], secoes: [], subsecoes: [] },

    // Navegação por slug
    findArtigoBySlug: (slug: string) => {
      if (!leiData) return null;
      const index = leiData.artigos.findIndex(a => a.slug === slug || a.slug.endsWith(slug));
      return index >= 0 ? { artigo: leiData.artigos[index], index } : null;
    },

    // Todos os artigos (para busca)
    allArtigos: leiData?.artigos ?? [],
    revokedOnlyMap: leiData?.revokedOnlyMap ?? {},
  };
}

// ============ HOOK: Lista de Leis ============

export function useLeis() {
  const [leisIndex, setLeisIndex] = useState<LeisIndex | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  useEffect(() => {
    const loadFromSupabase = async (): Promise<LeisIndex> => {
      const { data: rows, error: dbError } = await supabase
        .from('leis')
        .select('id, nome, sigla, numero, data_publicacao, ementa, total_artigos')
        .order('nome', { ascending: true });

      if (dbError) {
        throw new Error(`Erro ao buscar leis: ${dbError.message}`);
      }

      const leis: LeiIndex[] = (rows || []).map(row => ({
        id: row.id,
        nome: row.nome || '',
        sigla: row.sigla || '',
        numero: row.numero || '',
        data: row.data_publicacao || '',
        ementa: row.ementa || '',
        total_artigos: row.total_artigos || 0,
      }));

      return { leis };
    };

    const loadFromLocalJson = async (): Promise<LeisIndex> => {
      const response = await fetch('/data/leis/index.json');

      if (!response.ok) {
        throw new Error('Índice de leis não encontrado');
      }

      return await response.json();
    };

    const loadIndex = async () => {
      try {
        setIsLoading(true);

        const data = USE_SUPABASE
          ? await loadFromSupabase()
          : await loadFromLocalJson();

        setLeisIndex(data);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err : new Error('Erro ao carregar índice de leis'));
        setLeisIndex(null);
      } finally {
        setIsLoading(false);
      }
    };

    loadIndex();
  }, []);

  return {
    leis: leisIndex?.leis ?? [],
    isLoading,
    error,

    // Helper para encontrar lei por ID
    findLei: (id: string) => leisIndex?.leis.find(l => l.id === id) ?? null,
  };
}
