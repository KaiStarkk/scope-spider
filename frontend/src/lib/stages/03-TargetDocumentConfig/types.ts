export interface DocumentType {
  id: number;
  name: string;
  fileType: 'pdf' | 'xlsx' | 'either';
  terms: string[];
}

export interface DocumentScrapingConfig {
  globalTerms: string[];
  sequentialSearchTerms: string[];
  documentTypes: DocumentType[];
  stripTickerSuffix: boolean;
  stripFromCompanyName: string[];
  requiredInTitle: string[];
  includeCompanyName: boolean;
  includeStockTicker: boolean;
  tryWithoutQuotes: boolean;
  withoutQuotesPreference: string | null;
  engines: string[];
}
