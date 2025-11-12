<script lang="ts">
	import FileUploadView from './FileUploadView.svelte';
	import DataMappingView from './DataMappingView.svelte';
	import { runState } from '$lib/shared/stores/run.svelte';

	let headerRow = $state<string[]>([]);

	const companyData = $derived(runState.state.companyData);

	async function handleFileParsed(detail: { fileName: string; data: any[][]; file: File }) {
		const { fileName, data: rawData, file } = detail;

		if (rawData.length === 0) {
			runState.setCompanyData(fileName, []);
			return;
		}

		// Data Cleaning Logic
		let headerIndex = -1;
		const maxCols = rawData.reduce((max: number, row: any[]) => Math.max(max, row.length), 0);

		for (let i = 0; i < rawData.length; i++) {
			const row = rawData[i];
			const isUnique = new Set(row.map((cell: any) => String(cell).trim())).size === row.length;
			if (
				row.length === maxCols &&
				row.every((cell: any) => cell != null && String(cell).trim() !== '') &&
				isUnique
			) {
				headerIndex = i;
				break;
			}
		}

		if (headerIndex === -1) {
			runState.setCompanyData(fileName, rawData);
			return;
		}

		const detectedHeader = rawData[headerIndex];
		const cleanedData = [detectedHeader];
		const tickerRegex = /^[A-Z]{3}-AU$/;

		for (let i = headerIndex + 1; i < rawData.length; i++) {
			const row = rawData[i];
			if (row[0] && typeof row[0] === 'string' && tickerRegex.test(row[0])) {
				cleanedData.push(row);
			}
		}
		runState.setCompanyData(fileName, cleanedData);

		// Persist original file to backend for restoration on reload
		try {
			const formData = new FormData();
			formData.append('project_directory', runState.state.projectName!);
			formData.append('filename', fileName);
			formData.append('file', file);
			formData.append('folder', 'input');
			await fetch('/api/documents/upload', { method: 'POST', body: formData });
		} catch (e) {
			console.error('Failed to persist uploaded file:', e);
		}
	}

	function handleReset() {
		runState.newRun(runState.state.projectName!);
	}

	function handleVisibilityChange(rows: boolean[], cols: boolean[]) {
		runState.updateVisibility(rows, cols);
	}

	$effect(() => {
		// On resume or reload, if we have a saved fileName but no data, attempt to rehydrate by parsing saved Input data file
		const fileName = companyData.fileName;
		const hasData = companyData.data.length > 0;
		const project = runState.state.projectName;
		if (project && fileName && !hasData) {
			(async () => {
				try {
					// Check if the file exists in Input data
					const res = await fetch(
						`/api/documents/list?project_directory=${encodeURIComponent(project)}&folder=input`
					);
					if (!res.ok) return;
					const { documents } = await res.json();
					if (!documents.includes(fileName)) return;

					// Fetch the file from the server via download endpoint
					const url = `/datasets/${encodeURIComponent(project)}/Input data/${encodeURIComponent(fileName)}`;
					// Fallback: try raw fetch via server list isn't exposed; leave as presence check only
					// For rehydration, leave UI showing filename and keep mapping UI enabled
				} catch (e) {
					console.error('Failed to rehydrate input file:', e);
				}
			})();
		}
	});
</script>

<div class="card preset-tonal space-y-4 p-4">
	{#if companyData.data.length === 0}
		<FileUploadView
			fileName={companyData.fileName}
			onFileParsed={handleFileParsed}
			onReset={handleReset}
		/>
	{:else}
		<DataMappingView {companyData} bind:headerRow onVisibilityChange={handleVisibilityChange} />
	{/if}
</div>
