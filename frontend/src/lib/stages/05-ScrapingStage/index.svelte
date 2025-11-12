<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';
	import { browser } from '$app/environment';
	import { toaster } from '$lib/shared/stores/toast';
	import Check from '@lucide/svelte/icons/check';
	import ThumbsDown from '@lucide/svelte/icons/thumbs-down';
	import ThumbsUp from '@lucide/svelte/icons/thumbs-up';
	import X from '@lucide/svelte/icons/x';

	type DocStatus = {
		status: 'idle' | 'processing' | 'pending_review' | 'accepted' | 'failed';
		summary?: any;
		error?: string;
	};

	let documents = $state<string[]>([]);
	let docStates = $state<Record<string, DocStatus>>({});
	let isLoading = $state(true);
	let selected = $state<Record<string, boolean>>({});
	let isBatchProcessing = $state(false);
	let batchAbortController: AbortController | null = null;

	$effect(() => {
		// Validation for this step: any documents processed is fine for progression
		if (documents.length > 0) {
			const isComplete = documents.every((doc) => docStates[doc]?.status === 'accepted');
			runState.setStepValidity(4, isComplete);
		} else {
			runState.setStepValidity(4, true);
		}
	});

	$effect(() => {
		// Persist scraping states
		const snapshot: Record<string, { status: string; error?: string; summary?: any }> = {};
		for (const doc of Object.keys(docStates)) {
			const s = docStates[doc];
			snapshot[doc] = { status: s.status, error: s.error, summary: s.summary };
		}
		runState.setScrapingStates(snapshot);
	});

	function toggleSelectAll(checked: boolean) {
		const newSelected = { ...selected };
		for (const doc of documents) newSelected[doc] = checked;
		selected = newSelected;
	}

	$effect(() => {
		async function loadDocuments() {
			isLoading = true;
			if (browser) {
				const projectDirectory = runState.state.projectName;
				if (!projectDirectory) {
					toaster.error({
						title: 'Project Not Set',
						description: 'Cannot load documents without a project directory.'
					});
					isLoading = false;
					return;
				}

				try {
					const response = await fetch(
						`/api/documents/list?project_directory=${encodeURIComponent(projectDirectory)}`
					);
					if (!response.ok) {
						const err = await response.json();
						throw new Error(err.detail || 'Failed to list documents.');
					}
					const data = await response.json();
					documents = data.documents;

					const saved = runState.state.scraping?.states || {};
					const initialStates: Record<string, DocStatus> = {};
					for (const doc of documents) {
						initialStates[doc] = saved[doc]
							? {
									status: saved[doc].status as any,
									summary: saved[doc].summary,
									error: saved[doc].error
								}
							: { status: 'idle' };
					}
					docStates = initialStates;
				} catch (error) {
					const message = error instanceof Error ? error.message : 'An unknown error occurred.';
					toaster.error({ title: 'Loading Failed', description: message });
				}
			}
			isLoading = false;
		}
		loadDocuments();
	});

	async function handleProcess(filename: string) {
		const state = docStates[filename];
		state.status = 'processing';

		try {
			const projectDirectory = runState.state.projectName;
			if (!projectDirectory) {
				throw new Error('Project directory not set.');
			}

			const response = await fetch('/api/documents/process', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					project_directory: projectDirectory,
					document_filename: filename
				})
			});

			if (!response.ok) {
				const err = await response.json();
				throw new Error(err.detail || 'Processing failed.');
			}

			const result = await response.json();
			state.summary = result;
			state.status = 'pending_review';
			toaster.success({
				title: 'Processing Complete',
				description: `${filename} is ready for review.`
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : 'An unknown error occurred.';
			toaster.error({ title: 'Processing Failed', description: message });
			state.error = message;
			state.status = 'failed';
		}
	}

	function handleAccept(filename: string) {
		const state = docStates[filename];
		state.status = 'accepted';
	}

	function handleReject(filename: string) {
		const state = docStates[filename];
		state.status = 'idle';
		state.summary = undefined;
		state.error = undefined;
	}
</script>

<div class="card preset-tonal space-y-4 p-4">
	<h3 class="h3">Scraping Stage</h3>

	{#if isLoading}
		<p>Loading documents...</p>
	{:else if documents.length === 0}
		<p>No documents found to process. Please download documents in the previous step.</p>
	{:else}
		<div class="space-y-4">
			<div class="table-container h-[60vh] overflow-auto">
				<table class="table-hover table">
					<thead>
						<tr>
							<th>
								<input
									type="checkbox"
									class="checkbox"
									checked={Object.values(selected).every(Boolean) && documents.length > 0}
									indeterminate={!Object.values(selected).every(Boolean) &&
										Object.values(selected).some(Boolean)}
									onchange={(e) => toggleSelectAll(e.currentTarget.checked)}
								/>
							</th>
							<th>Document</th>
							<th>Status</th>
							<th>Details</th>
							<th>Actions</th>
						</tr>
					</thead>
					<tbody>
						{#each documents as doc (doc)}
							{@const state = docStates[doc]}
							<tr>
								<td><input type="checkbox" class="checkbox" bind:checked={selected[doc]} /></td>
								<td class="truncate" title={doc}>{doc}</td>
								<td>
									{#if state}
										<span
											class="badge {state.status === 'accepted'
												? 'preset-filled-success'
												: state.status === 'failed'
													? 'preset-filled-error'
													: 'preset-filled-surface'}">{state.status.replace('_', ' ')}</span
										>
									{/if}
								</td>
								<td>
									{#if state?.status === 'pending_review' || state?.status === 'accepted'}
										<pre
											class="bg-surface-300 dark:bg-surface-700 max-h-24 overflow-auto rounded p-1 font-mono">{JSON.stringify(
												state.summary,
												null,
												2
											)}</pre>
									{:else if state?.status === 'failed'}
										<p class="text-error-500 text-xs" title={state.error}>{state.error}</p>
									{/if}
								</td>
								<td>
									{#if state}
										{#if state.status === 'idle'}
											<button class="btn btn-sm preset-filled" onclick={() => handleProcess(doc)}
												>Process</button
											>
										{:else if state.status === 'processing'}
											<p>Processing...</p>
										{:else if state.status === 'failed'}
											<button
												class="btn btn-sm preset-filled-error"
												onclick={() => handleProcess(doc)}>Retry</button
											>
										{:else if state.status === 'pending_review'}
											<button
												class="btn btn-sm preset-filled-success"
												onclick={() => handleAccept(doc)}><ThumbsUp class="h-4 w-4" /></button
											>
											<button
												class="btn btn-sm preset-filled-error"
												onclick={() => handleReject(doc)}><ThumbsDown class="h-4 w-4" /></button
											>
										{:else if state.status === 'accepted'}
											<div class="text-success-500 flex items-center gap-2">
												<Check class="h-5 w-5" /><span>Accepted</span>
											</div>
										{/if}
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
	{/if}
</div>
