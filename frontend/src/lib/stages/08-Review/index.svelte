<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';
	import { browser } from '$app/environment';
	import { toaster } from '$lib/shared/stores/toast';
	import { ArrowDown, ArrowUp } from '@lucide/svelte/icons';

	type SortState = {
		key: string;
		direction: 'asc' | 'desc';
	};

	let allExtractions = $state<any[]>([]);
	let headers = $state<string[]>([]);
	let isLoading = $state(true);
	let sortState = $state<SortState | null>(null);
	let filterTerm = $state('');

	const filteredAndSortedExtractions = $derived(() => {
		let data = [...allExtractions];

		// Filtering
		if (filterTerm) {
			const lowercasedFilter = filterTerm.toLowerCase();
			data = data.filter((row) =>
				Object.values(row).some((value) => String(value).toLowerCase().includes(lowercasedFilter))
			);
		}

		// Sorting
		if (sortState) {
			data.sort((a, b) => {
				const aValue = a[sortState!.key];
				const bValue = b[sortState!.key];

				if (aValue < bValue) {
					return sortState!.direction === 'asc' ? -1 : 1;
				}
				if (aValue > bValue) {
					return sortState!.direction === 'asc' ? 1 : -1;
				}
				return 0;
			});
		}
		return data;
	});

	$effect(() => {
		async function loadExtractions() {
			console.log('Step 8 loadExtractions: runState.state.projectName', runState.state.projectName);
			isLoading = true;
			if (browser) {
				const projectDirectory = runState.state.projectName;
				if (!projectDirectory) {
					isLoading = false;
					return;
				}

				try {
					// This is a temporary solution for the frontend display.
					// We'll call the export endpoint with a special parameter to get the data as JSON
					// without triggering a download. A dedicated endpoint would be better in a real app.
					const response = await fetch(
						`/api/export/json?project_directory=${encodeURIComponent(projectDirectory)}`
					);
					if (!response.ok) {
						const err = await response.json();
						throw new Error(err.detail || 'Failed to load aggregated data.');
					}
					const data = await response.json();
					allExtractions = data;

					if (data.length > 0) {
						headers = Object.keys(data[0]);
					}
				} catch (error) {
					// Don't show an error if no data is found, as it's an expected state.
					if (!(error instanceof Error && error.message.includes('No extracted data'))) {
						const message = error instanceof Error ? error.message : 'An unknown error occurred.';
						toaster.error({ title: 'Loading Failed', description: message });
					}
					allExtractions = [];
				}
			}
			isLoading = false;
		}
		loadExtractions();
	});

	function handleSort(key: string) {
		if (sortState?.key === key) {
			sortState.direction = sortState.direction === 'asc' ? 'desc' : 'asc';
		} else {
			sortState = { key, direction: 'asc' };
		}
	}

	async function handleExport(format: 'csv' | 'json') {
		const projectDirectory = runState.state.projectName;
		if (!projectDirectory) {
			toaster.error({ title: 'Export Failed', description: 'Project directory not set.' });
			return;
		}

		try {
			const response = await fetch(
				`/api/export/${format}?project_directory=${encodeURIComponent(projectDirectory)}`
			);

			if (!response.ok) {
				const err = await response.json();
				throw new Error(err.detail || `Failed to export ${format.toUpperCase()}.`);
			}

			const blob = await response.blob();
			const url = window.URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download =
				response.headers.get('content-disposition')?.split('filename=')[1] || `export.${format}`;
			document.body.appendChild(a);
			a.click();
			a.remove();
			window.URL.revokeObjectURL(url);

			toaster.success({
				title: 'Export Successful',
				description: `Your ${format.toUpperCase()} file has been downloaded.`
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : 'An unknown error occurred.';
			toaster.error({ title: 'Export Failed', description: message });
		}
	}
</script>

<div class="card preset-tonal space-y-4 p-4">
	<div class="flex items-center justify-between">
		<h3 class="h3">Final Analysis & Export</h3>
		<div class="flex gap-2">
			<button class="btn preset-filled" onclick={() => handleExport('csv')}>Export CSV</button>
			<button class="btn preset-filled" onclick={() => handleExport('json')}>Export JSON</button>
		</div>
	</div>

	{#if isLoading}
		<p>Loading extracted data...</p>
	{:else if allExtractions.length === 0}
		<p class="text-center">
			No data has been extracted yet. Please complete the previous steps to see aggregated results
			here.
		</p>
	{:else}
		<div class="space-y-4">
			<input class="input" type="text" bind:value={filterTerm} placeholder="Filter table data..." />
			<div class="table-container overflow-x-auto">
				<table class="table-hover table">
					<thead>
						<tr>
							{#each headers as header}
								<th onclick={() => handleSort(header)} class="cursor-pointer">
									<div class="flex items-center gap-2">
										{header}
										{#if sortState?.key === header}
											{#if sortState.direction === 'asc'}
												<ArrowUp class="h-4 w-4" />
											{:else}
												<ArrowDown class="h-4 w-4" />
											{/if}
										{/if}
									</div>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each filteredAndSortedExtractions() as row}
							<tr>
								{#each headers as header}
									<td>{row[header] || ''}</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{/if}
</div>
