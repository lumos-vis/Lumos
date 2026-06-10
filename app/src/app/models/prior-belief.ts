export interface Bin {
  lo: number;
  hi: number;
  label: string;   // would be something like "$20K–$60K"
}

export interface PriorBelief {
  datasetId: string;
  attribute: string;
  binEdges: number[];
  counts: number[];
  ballCount: number;
  columnHash: string;
  createdAt: number;
  categories?: string[];  // present for categorical attributes; one entry per bin
}

