<script lang="ts">
	import type { Company, DocumentConfig, TableState, SelectionInfo } from './types';
	import { createEventDispatcher } from 'svelte';
	import Search from '@lucide/svelte/icons/search';
	import Download from '@lucide/svelte/icons/download';
	import Check from '@lucide/svelte/icons/check';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
	import { runState } from '$lib/shared/stores/run.svelte';

	let {
		companies,
		docTypes,
		tableState,
		selectionInfo,
		selected = $bindable(),
		batchOperation
	}: {
		companies: Company[];
		docTypes: DocumentConfig[];
		tableState: TableState;
		selectionInfo: SelectionInfo;
		selected: Record<string, boolean>;
		batchOperation: 'search' | 'download' | null;
	} = $props();

	const dispatch = createEventDispatcher();

	function dispatchSelection(type: 'all' | 'company' | 'docType', checked: boolean, payload?: any) {
		dispatch('selectionChange', { type, checked, payload });
	}
</script>

<div class="table-container flex-grow overflow-auto">
	<table class="table-hover table">
		<thead>
			<tr>
				<th class="variant-soft sticky left-0 w-12">
					<input
						type="checkbox"
						class="checkbox"
						checked={selectionInfo.selectAllState.checked}
						indeterminate={selectionInfo.selectAllState.indeterminate}
						onchange={(e) => dispatchSelection('all', e.currentTarget.checked)}
						title="Select All"
					/>
				</th>
				<th class="variant-soft sticky left-12">Company Name</th>
				{#each docTypes as doc}
					<th>
						<div class="flex items-center gap-2">
							<input
								type="checkbox"
								class="checkbox"
								checked={selectionInfo.docTypeStates[doc.name]?.checked}
								indeterminate={selectionInfo.docTypeStates[doc.name]?.indeterminate}
								onchange={(e) => dispatchSelection('docType', e.currentTarget.checked, doc)}
								title={`Select all ${doc.name}`}
							/>
							<span>{doc.name}</span>
						</div>
					</th>
				{/each}
			</tr>
		</thead>
		<tbody>
			{#each companies as company (company.stock_ticker)}
				<tr>
					<td class="variant-soft sticky left-0">
						<input
							type="checkbox"
							class="checkbox"
							checked={selectionInfo.companyStates[company.stock_ticker]?.checked}
							indeterminate={selectionInfo.companyStates[company.stock_ticker]?.indeterminate}
							onchange={(e) => dispatchSelection('company', e.currentTarget.checked, company)}
						/>
					</td>
					<td class="variant-soft sticky left-12 whitespace-nowrap">{company.company_name}</td>
					{#each docTypes as doc (doc.name)}
						{@const cell = tableState[company.stock_ticker]?.[doc.name]}
						{@const key = `${company.stock_ticker}-${doc.name}`}
						{@const isSelected = !!selected[key]}
						<td>
							<div class="flex items-start gap-2">
								<input
									type="checkbox"
									class="checkbox"
									checked={isSelected}
									onchange={(e) => {
										selected[key] = e.currentTarget.checked;
									}}
								/>
								<div class="flex-grow">
									{#if batchOperation === 'search' && isSelected && (cell?.status === 'idle' || cell?.status === 'failed')}
										<span class="text-surface-500 text-sm">Queued for search...</span>
									{:else if batchOperation === 'download' && isSelected && cell?.status === 'found'}
										<span class="text-surface-500 text-sm">Queued for download...</span>
									{:else if !cell || cell.status === 'idle' || cell.status === 'failed'}
										<button
											class="btn btn-sm variant-filled"
											onclick={() => dispatch('search', { company, doc })}
										>
											<Search class="mr-2 h-4 w-4" />
											{cell?.status === 'failed' ? 'Retry Search' : 'Search'}
										</button>
										{#if cell?.status === 'failed'}
											<p class="text-error-500 truncate text-xs" title={cell.error}>
												Error: {cell.error}
											</p>
										{/if}
									{:else if cell.status === 'searching'}
										<span class="text-sm font-semibold">Searching...</span>
									{:else if cell.status === 'found'}
										<a
											href={cell.url}
											target="_blank"
											class="anchor block max-w-xs truncate text-sm">{cell.url}</a
										>
										<button
											class="btn btn-sm variant-filled-primary mt-1"
											onclick={() => dispatch('download', { company, doc })}
										>
											<Download class="mr-2 h-4 w-4" />
											Download
										</button>
									{:else if cell.status === 'downloading'}
										<span class="text-sm font-semibold">Downloading...</span>
									{:else if cell.status === 'complete'}
										<p class="text-success-500 flex items-center gap-1 text-sm">
											<Check class="h-4 w-4" /> Downloaded
										</p>
										{#if cell.path}
											<a
												class="anchor block max-w-44 truncate text-xs"
												title={cell.path}
												href={`/api/documents/raw-file?project_directory=${encodeURIComponent(runState.state.projectName || '')}&filename=${encodeURIComponent(cell.path || '')}`}
												target="_blank">{cell.path}</a
											>
										{/if}
									{/if}
								</div>
							</div>
						</td>
					{/each}
				</tr>
			{/each}
		</tbody>
	</table>
</div>
