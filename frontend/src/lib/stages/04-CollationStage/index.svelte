<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';
	import { browser } from '$app/environment';
	import { toaster } from '$lib/shared/stores/toast';
	import { onMount } from 'svelte';
	import type { Company, DocumentConfig, CellState, SelectionInfo, TableState } from './types';
	import CollectionTable from './CollectionTable.svelte';
	import ActionPanel from './ActionPanel.svelte';

	let companies = $state<Company[]>([]);
	let docTypes = $state<DocumentConfig[]>([]);
	let tableState = $state<TableState>({});
	let isLoading = $state(true);
	let selected = $state<Record<string, boolean>>({});
	let batchOperation = $state<'search' | 'download' | null>(null);

	const isBatchProcessing = $derived(batchOperation !== null);

	// Simpler derived selection and enablement flags
	const selectedKeys = $derived(
		Object.entries(selected)
			.filter(([, isSelected]) => isSelected)
			.map(([key]) => key)
	);

	const canBatchSearch = $derived(
		selectedKeys.some((key) => {
			const idx = key.lastIndexOf('-');
			if (idx <= 0) return false;
			const ticker = key.slice(0, idx);
			const docName = key.slice(idx + 1);
			const status = tableState[ticker]?.[docName]?.status;
			return status === 'idle' || status === 'failed';
		})
	);

	const canBatchDownload = $derived(
		selectedKeys.some((key) => {
			const idx = key.lastIndexOf('-');
			if (idx <= 0) return false;
			const ticker = key.slice(0, idx);
			const docName = key.slice(idx + 1);
			return tableState[ticker]?.[docName]?.status === 'found';
		})
	);

	async function persistCellState(ticker: string, docName: string) {
		const projectDirectory = runState.state.projectName;
		const cellState = tableState[ticker]?.[docName];
		if (!projectDirectory || !cellState) return;

		// Don't persist transitional states
		if (cellState.status === 'searching' || cellState.status === 'downloading') return;

		try {
			const response = await fetch('/api/documents/update-cell-state', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					project_directory: projectDirectory,
					cell_key: `${ticker}-${docName}`,
					cell_state: cellState
				})
			});
			if (!response.ok) throw new Error((await response.json()).detail || 'Failed to sync state');
		} catch (e) {
			const message = e instanceof Error ? e.message : 'An unknown error occurred';
			toaster.error({ title: 'State Sync Failed', description: message });
		}
	}

	$effect(() => {
		const atLeastOneComplete =
			companies.length > 0 &&
			companies.some((c) =>
				docTypes.some((d) => tableState[c.stock_ticker]?.[d.name]?.status === 'complete')
			);
		runState.setStepValidity(3, atLeastOneComplete);
	});

	const selectionInfo: SelectionInfo = $derived({
		get selectedKeys() {
			return selectedKeys;
		},
		get selectAllState() {
			const totalCells = companies.length * docTypes.length;
			if (totalCells === 0) return { checked: false, indeterminate: false };
			const selectedCount = selectedKeys.length;
			return {
				checked: selectedCount === totalCells,
				indeterminate: selectedCount > 0 && selectedCount < totalCells
			};
		},
		get companyStates() {
			const states: Record<string, { checked: boolean; indeterminate: boolean }> = {};
			for (const c of companies) {
				const keys = docTypes.map((d) => `${c.stock_ticker}-${d.name}`);
				const selectedCount = keys.filter((key) => selected[key]).length;
				states[c.stock_ticker] = {
					checked: selectedCount === docTypes.length,
					indeterminate: selectedCount > 0 && selectedCount < docTypes.length
				};
			}
			return states;
		},
		get docTypeStates() {
			const states: Record<string, { checked: boolean; indeterminate: boolean }> = {};
			for (const d of docTypes) {
				const keys = companies.map((c) => `${c.stock_ticker}-${d.name}`);
				const selectedCount = keys.filter((key) => selected[key]).length;
				states[d.name] = {
					checked: selectedCount === companies.length,
					indeterminate: selectedCount > 0 && selectedCount < companies.length
				};
			}
			return states;
		},
		get canBatchSearch() {
			return canBatchSearch;
		},
		get canBatchDownload() {
			return canBatchDownload;
		}
	});

	async function handleSearch(company: Company, doc: DocumentConfig) {
		tableState[company.stock_ticker][doc.name].status = 'searching';
		try {
			const response = await fetch('/api/documents/search', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					project_directory: runState.state.projectName,
					company_name: company.company_name,
					stock_ticker: company.stock_ticker,
					doc_type: doc.name,
					search_terms: doc.terms,
					file_type: doc.fileType
				})
			});
			if (!response.ok) throw new Error((await response.json()).detail || 'Search failed');
			const result = await response.json();
			const cell = tableState[company.stock_ticker][doc.name];
			cell.status = 'found';
			cell.url = result.url;
		} catch (e) {
			const cell = tableState[company.stock_ticker][doc.name];
			cell.status = 'failed';
			cell.error = e instanceof Error ? e.message : 'Unknown error';
			toaster.error({ title: 'Search Failed', description: cell.error });
		} finally {
			await persistCellState(company.stock_ticker, doc.name);
		}
	}

	async function handleDownload(company: Company, doc: DocumentConfig) {
		const cell = tableState[company.stock_ticker][doc.name];
		if (!cell.url) return;
		cell.status = 'downloading';
		try {
			const ext =
				doc.fileType === 'either'
					? cell.url?.toLowerCase().endsWith('.xlsx')
						? 'xlsx'
						: 'pdf'
					: doc.fileType;
			const filename = `${company.company_name}_${doc.name}.${ext}`.replace(/[^\w.-]/g, '_');
			const response = await fetch('/api/documents/download', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					project_directory: runState.state.projectName,
					url: cell.url,
					filename,
					stock_ticker: company.stock_ticker,
					doc_type: doc.name
				})
			});
			if (!response.ok) throw new Error((await response.json()).detail || 'Download failed');
			const result = await response.json();
			cell.status = 'complete';
			cell.path = result.path;
			toaster.success({ title: 'Download Succeeded', description: filename });
		} catch (e) {
			cell.status = 'failed';
			cell.error = e instanceof Error ? e.message : 'Unknown error';
			toaster.error({ title: 'Download Failed', description: cell.error });
		} finally {
			await persistCellState(company.stock_ticker, doc.name);
		}
	}

	async function handleBatchSearch() {
		batchOperation = 'search';
		const items = selectionInfo.selectedKeys
			.map((key) => {
				const idx = key.lastIndexOf('-');
				if (idx <= 0) return null;
				const ticker = key.slice(0, idx);
				const docName = key.slice(idx + 1);
				const company = companies.find((c) => c.stock_ticker === ticker);
				const doc = docTypes.find((d) => d.name === docName);
				const cell = tableState[ticker]?.[docName];
				if (!company || !doc || !cell || (cell.status !== 'idle' && cell.status !== 'failed'))
					return null;
				return { company, doc };
			})
			.filter(Boolean);

		for (const item of items) {
			await handleSearch(item!.company, item!.doc);
		}
		batchOperation = null;
	}

	async function handleBatchDownload() {
		batchOperation = 'download';
		const items = selectionInfo.selectedKeys
			.map((key) => {
				const idx = key.lastIndexOf('-');
				if (idx <= 0) return null;
				const ticker = key.slice(0, idx);
				const docName = key.slice(idx + 1);
				const company = companies.find((c) => c.stock_ticker === ticker);
				const doc = docTypes.find((d) => d.name === docName);
				const cell = tableState[ticker]?.[docName];
				if (!company || !doc || !cell || cell.status !== 'found') return null;
				return { company, doc };
			})
			.filter(Boolean);

		for (const item of items) {
			await handleDownload(item!.company, item!.doc);
		}
		batchOperation = null;
	}

	onMount(async () => {
		console.log('Step 4 onMount: runState.state.projectName', runState.state.projectName);
		console.log(
			'Step 4 onMount: runState.state.companyData',
			JSON.parse(JSON.stringify(runState.state.companyData))
		);
		isLoading = true;
		const projectDirectory = runState.state.projectName;
		if (!browser || !projectDirectory) {
			isLoading = false;
			return;
		}
		try {
			companies = runState.state.companyData.processedCompanies ?? [];
			const cfgRes = await fetch(
				`/api/config/documents?project_directory=${encodeURIComponent(projectDirectory)}`
			);
			const config = await cfgRes.json();
			docTypes = config.documentTypes || [];

			const stateRes = await fetch(
				`/api/project/state?project_name=${encodeURIComponent(projectDirectory)}`
			);
			const state = await stateRes.json();
			const serverCells = state?.documentCollection?.cells || {};

			const initial: TableState = {};
			for (const company of companies) {
				initial[company.stock_ticker] = {};
				for (const doc of docTypes) {
					initial[company.stock_ticker][doc.name] = serverCells[company.stock_ticker]?.[
						doc.name
					] || { status: 'idle' };
				}
			}
			tableState = initial;
		} catch (e) {
			toaster.error({
				title: 'Failed to load project data',
				description: e instanceof Error ? e.message : 'Unknown error'
			});
		} finally {
			isLoading = false;
		}
	});

	function handleSelectionChange(e: CustomEvent) {
		const { type, checked, payload } = e.detail;
		const next = { ...selected };
		switch (type) {
			case 'all':
				for (const c of companies) {
					for (const d of docTypes) {
						next[`${c.stock_ticker}-${d.name}`] = checked;
					}
				}
				break;
			case 'company':
				for (const d of docTypes) {
					next[`${payload.stock_ticker}-${d.name}`] = checked;
				}
				break;
			case 'docType':
				for (const c of companies) {
					next[`${c.stock_ticker}-${payload.name}`] = checked;
				}
				break;
		}
		selected = next;
	}
</script>

<div class="card variant-ghost-surface space-y-4 p-4">
	<h3 class="h3">Collation Stage</h3>
	{#if isLoading}
		<p>Loading project data...</p>
	{:else if companies.length === 0 || docTypes.length === 0}
		<p>No company data or document configurations found. Please complete the previous steps.</p>
	{:else}
		<div class="flex h-[70vh] gap-4">
			<CollectionTable
				{companies}
				{docTypes}
				{tableState}
				{selectionInfo}
				{batchOperation}
				bind:selected
				on:selectionChange={handleSelectionChange}
				on:search={(e) => handleSearch(e.detail.company, e.detail.doc)}
				on:download={(e) => handleDownload(e.detail.company, e.detail.doc)}
			/>
			<div class="w-64 flex-shrink-0">
				<ActionPanel
					{isBatchProcessing}
					{batchOperation}
					canBatchSearch={selectionInfo.canBatchSearch}
					canBatchDownload={selectionInfo.canBatchDownload}
					on:batchSearch={handleBatchSearch}
					on:batchDownload={handleBatchDownload}
				/>
			</div>
		</div>
	{/if}
</div>
