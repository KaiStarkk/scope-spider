import { browser } from '$app/environment';
import { toaster } from '$lib/shared/stores/toast';

const STEPS = [
  {
    label: 'Project Selection',
    description: 'Choose an existing project or create a new one to begin.',
  },
  {
    label: 'Import Companies',
    description: 'Import company data from a CSV or Excel file.',
  },
  {
    label: 'Document Configuration',
    description: 'Configure the types of documents and search terms to be used.',
  },
  {
    label: 'Document Collection',
    description: 'Search for and download the required documents for each company.',
  },
  {
    label: 'Transcribe',
    description: 'Transcribe downloaded documents; clean Excel and structure PDFs by page.',
  },
  {
    label: 'Extract',
    description: 'Select only relevant pages/rows for analysis based on keywords.',
  },
  {
    label: 'Analyse',
    description: 'Use AI to find emissions and carbon credit details.',
  },
  {
    label: 'Review',
    description: 'Review, sort, and export the final structured dataset.',
  }
];

export type Step = {
  label: string;
  description: string;
};

export type CompanyData = {
  fileName: string | null;
  data: any[][];
  visibleRows: boolean[];
  visibleColumns: boolean[];
  mappings: Record<string, string>;
  processedCompanies?: any[] | null;
};

type RunState = {
  projectName: string | null;
  currentStep: number;
  maxStep: number;
  stepsValidity: boolean[];
  companyData: {
    fileName: string | null;
    data: any[][];
    visibleRows: boolean[];
    visibleColumns: boolean[];
    mappings: Record<string, string>;
    processedCompanies: any[] | null;
  };
  documentCollection?: {
    selected: Record<string, boolean>;
  };
  filterConfig?: any;
  aiConfig?: any;
  saveTimeout?: number;
  // internal snapshot to prevent redundant saves
  lastSavedSnapshot?: string;
};

function createRunState() {
  let state = $state<RunState>(getInitialState());

  function getInitialState(): RunState {
    return {
      projectName: null,
      currentStep: 0,
      maxStep: 0,
      stepsValidity: new Array(STEPS.length).fill(false),
      companyData: {
        fileName: null,
        data: [],
        visibleRows: [],
        visibleColumns: [],
        mappings: {},
        processedCompanies: null
      },
      documentCollection: { selected: {} }
    };
  }

  function debouncedSaveState(delay: number = 500) {
    if (state.saveTimeout) {
      window.clearTimeout(state.saveTimeout);
    }
    state.saveTimeout = window.setTimeout(() => {
      // Only save once after burst of changes
      saveState();
    }, delay);
  }

  function snapshotSerializable(): string {
    const { saveTimeout, lastSavedSnapshot, ...rest } = state as any;
    return JSON.stringify(rest);
  }

  async function loadRun(projectName: string) {
    try {
      const response = await fetch(
        `/api/project/state?project_name=${encodeURIComponent(projectName)}`
      );
      if (!response.ok) {
        throw new Error('Failed to load project state.');
      }
      const projectData = await response.json();
      // sanitize any internal keys that may have been persisted previously
      delete (projectData as any).saveTimeout;
      delete (projectData as any).lastSavedSnapshot;
      // Ensure the loaded state has the correct shape by explicitly assigning properties
      const initialState = getInitialState();
      state.projectName = projectName; // The name from the URL param is the source of truth
      state.currentStep = projectData.currentStep ?? initialState.currentStep;
      state.maxStep = projectData.maxStep ?? initialState.maxStep;
      state.stepsValidity = projectData.stepsValidity ?? initialState.stepsValidity;
      state.companyData = projectData.companyData ?? initialState.companyData;
      state.documentCollection = projectData.documentCollection ?? initialState.documentCollection;
      // initialize snapshot to loaded state to avoid immediate redundant save
      state.lastSavedSnapshot = snapshotSerializable();
      console.log('runState: Project loaded', JSON.parse(snapshotSerializable()));
    } catch (error) {
      console.error('Error loading project, starting fresh:', error);
      toaster.error({
        title: 'Load Failed',
        description: 'Could not load project state. Starting a new configuration for this project.'
      });
      newRun(projectName);
    }
  }

  function newRun(projectName: string | null) {
    const initialState = getInitialState();
    state.projectName = projectName;
    state.currentStep = initialState.currentStep;
    state.maxStep = initialState.maxStep;
    state.stepsValidity = initialState.stepsValidity;
    state.companyData = initialState.companyData;
    state.documentCollection = initialState.documentCollection;
    state.lastSavedSnapshot = snapshotSerializable();
    console.log('runState: New run created', JSON.parse(snapshotSerializable()));
  }

  async function saveState(notify: boolean = false) {
    if (!state.projectName) return;
    const currentSnapshot = snapshotSerializable();
    if (state.lastSavedSnapshot === currentSnapshot) {
      return true; // No changes, skip POST
    }
    try {
      // create a sanitized payload without internal fields
      const { saveTimeout, lastSavedSnapshot, ...payload } = state as any;
      const res = await fetch('/api/project/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        throw new Error('Failed to save project state.');
      }
      if (notify) {
        toaster.success({ title: 'State Saved', description: 'Your progress has been saved.' });
      }
      state.lastSavedSnapshot = currentSnapshot;
      return true;
    } catch (error) {
      console.error("Error saving project state:", error);
      toaster.error({ title: "Save Failed", description: "Could not save project state to the server." });
      return false;
    }
  }

  // Pipeline navigation logic
  function setStep(step: number) {
    if (step > state.currentStep && step < STEPS.length && state.stepsValidity[state.currentStep]) {
      state.currentStep = step;
      if (state.currentStep > state.maxStep) {
        state.maxStep = state.currentStep;
      }
      debouncedSaveState(400);
    }
  }

  function nextStep() {
    if (state.currentStep < STEPS.length - 1 && state.stepsValidity[state.currentStep]) {
      state.currentStep++;
      if (state.currentStep > state.maxStep) {
        state.maxStep = state.currentStep;
      }
      debouncedSaveState(400);
    }
  }

  function prevStep() {
    if (state.currentStep > 0) {
      state.currentStep--;
    }
  }

  function setStepValidity(step: number, isValid: boolean) {
    if (step >= 0 && step < STEPS.length) {
      const prev = state.stepsValidity[step];
      if (prev !== isValid) {
        state.stepsValidity[step] = isValid;
        if (isValid && step + 1 > state.maxStep) {
          state.maxStep = step + 1;
        }
        debouncedSaveState(500);
      }
    }
  }

  return {
    get state() { return state },
    async getState() {
      // This is a simple getter that can be awaited if needed,
      // but direct access via .state should be preferred for reactivity.
      return JSON.parse(snapshotSerializable());
    },
    get steps() { return STEPS },
    loadRun,
    newRun,
    saveState,
    setStep,
    nextStep,
    prevStep,
    setStepValidity,
    updateFilterConfig(config: any) {
      state.filterConfig = { ...config };
      debouncedSaveState(600);
    },
    updateAiConfig(config: any) {
      state.aiConfig = { ...config };
      debouncedSaveState(600);
    },
    clearDocumentCollectionSelections() {
      state.documentCollection = { selected: {} };
      debouncedSaveState(400);
    },
    setDocumentCollectionSelections(selected: Record<string, boolean>) {
      state.documentCollection = { selected: { ...selected } };
      debouncedSaveState(600);
    },
    // Company Data specific actions
    setCompanyData(fileName: string, data: any[][]) {
      state.companyData.fileName = fileName;
      state.companyData.data = data;
      state.companyData.visibleRows = new Array(data.length).fill(true);
      state.companyData.visibleColumns = new Array(data[0]?.length || 0).fill(true);
      state.companyData.mappings = {}; // Reset mappings on new file
      state.companyData.processedCompanies = null; // Reset processed companies on new file
      // Invalidate all subsequent steps
      for (let i = 1; i < STEPS.length; i++) {
        state.stepsValidity[i] = false;
      }
      state.maxStep = 1;
      debouncedSaveState();
    },
    updateVisibility(rows: boolean[], cols: boolean[]) {
      state.companyData.visibleRows = rows;
      state.companyData.visibleColumns = cols;
      debouncedSaveState();
    },
    updateMappings(mappings: Record<string, string>) {
      // Avoid unnecessary saves when nothing changed
      const prev = state.companyData.mappings || {};
      const sameKeys = Object.keys(mappings).length === Object.keys(prev).length;
      let equal = sameKeys;
      if (equal) {
        for (const k in mappings) {
          if (mappings[k] !== prev[k]) { equal = false; break; }
        }
      }
      if (!equal) {
        state.companyData.mappings = { ...mappings };
      }
      // Auto-validate step 1 if mappings are complete
      const requiredMappings = ['company_name', 'stock_ticker', 'industry_classification'];
      const isComplete = requiredMappings.every(
        (key) => mappings[key] && mappings[key] !== ''
      );
      // Only trigger save when mappings actually changed
      if (!equal) {
        if (isComplete) {
          setStepValidity(1, true);
        } else {
          setStepValidity(1, false);
        }
        debouncedSaveState(600);
      }
    },
    async setProcessedCompanies(companies: any[]) {
      state.companyData.processedCompanies = companies;
      // When companies are set, invalidate subsequent steps
      for (let i = 2; i < STEPS.length; i++) {
        state.stepsValidity[i] = false;
      }
      state.maxStep = 2;
      console.log(
        'runState: Processed companies set',
        JSON.parse(snapshotSerializable())
      );
      await saveState(true);
    }
  };
}

export const runState = createRunState();
