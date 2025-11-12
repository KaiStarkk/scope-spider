import type { DocumentType as ExtractedDocumentType } from '../03-TargetDocumentConfig/types';

export type Company = {
  company_name: string;
  industry_classification: string;
  stock_ticker: string;
};

export type DocumentConfig = {
  name: string;
  terms: string[];
  fileType: 'pdf' | 'xlsx' | 'either';
};

export type CellState = {
  status: 'idle' | 'searching' | 'found' | 'downloading' | 'complete' | 'failed';
  url?: string;
  path?: string;
  error?: string;
};

export type TableState = Record<string, Record<string, CellState>>;

export type SelectionInfo = {
  selectedKeys: string[];
  selectAllState: { checked: boolean; indeterminate: boolean };
  companyStates: Record<string, { checked: boolean; indeterminate: boolean }>;
  docTypeStates: Record<string, { checked: boolean; indeterminate: boolean }>;
  canBatchSearch: boolean;
  canBatchDownload: boolean;
};
