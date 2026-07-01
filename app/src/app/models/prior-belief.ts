export interface Bin {
  lo: number;
  hi: number;
  label: string;   // would be something like "$20K–$60K"
}

export type PriorCondition = 'diagnosed' | 'not_diagnosed';

export interface PriorBelief {
  datasetId: string;
  attribute: string;
  condition: PriorCondition;   // which subgroup this belief applies to
  binEdges: number[];
  counts: number[];
  ballCount: number;
  columnHash: string;
  createdAt: number;
  confidence?: number;
  categories?: string[];
}

