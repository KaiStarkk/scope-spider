<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';
	import { browser } from '$app/environment';
	import { toaster } from '$lib/shared/stores/toast';
	import Check from '@lucide/svelte/icons/check';
	import Lock from '@lucide/svelte/icons/lock';
	import ThumbsDown from '@lucide/svelte/icons/thumbs-down';
	import ThumbsUp from '@lucide/svelte/icons/thumbs-up';
	import Unlock from '@lucide/svelte/icons/unlock';
	import X from '@lucide/svelte/icons/x';

	type DocStatus = {
		status: 'idle' | 'processing' | 'pending_review' | 'accepted' | 'failed';
		summary?: any;
		error?: string;
		isLocked?: boolean;
	};

	let documents = $state<string[]>([]);
	let docStates = $state<Record<string, DocStatus>>({});
	let isLoading = $state(true);
	let selected = $state<Record<string, boolean>>({});
	let isBatchProcessing = $state(false);
	let batchAbortController: AbortController | null = null;

	$effect(() => {
		// Validation for this step: all documents must be accepted.
		if (documents.length > 0) {
			const isComplete = documents.every((doc) => docStates[doc]?.status === 'accepted');
			runState.setStepValidity(4, isComplete);
		} else {
			// If there are no documents, the step isn't "complete" but it's not invalid.
			// Let's say it's valid to proceed if there's nothing to do.
			runState.setStepValidity(4, true);
		}
	});

	const selectionInfo = $derived(() => {
		const selectedFiles = documents.filter((doc) => selected[doc]);
		const selectedCount = selectedFiles.length;

		const selectAllState = {
			checked: selectedCount === documents.length && documents.length > 0,
			indeterminate: selectedCount > 0 && selectedCount < documents.length
		};

		const canBatchProcess = selectedFiles.some((doc) => {
			const status = docStates[doc]?.status;
			return status === 'idle' || status === 'failed';
		});

		return {
			selectAllState,
			canBatchProcess
		};
	});

	function toggleSelectAll(checked: boolean) {
		const newSelected = { ...selected };
		for (const doc of documents) {
			newSelected[doc] = checked;
		}
		selected = newSelected;
	}

	$effect(() => {
		async function loadDocuments() {
			console.log('Step 5 loadDocuments: runState.state.projectName', runState.state.projectName);
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

					const initialStates: Record<string, DocStatus> = {};
					for (const doc of documents) {
						initialStates[doc] = { status: 'idle', isLocked: false };
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

	async function handleBatchProcess() {
		isBatchProcessing = true;
		batchAbortController = new AbortController();
		const itemsToProcess = documents.filter((doc) => selected[doc]);
		let successCount = 0;
		let failureCount = 0;

		for (const doc of itemsToProcess) {
			if (batchAbortController.signal.aborted) {
				failureCount += itemsToProcess.length - successCount - failureCount;
				break;
			}

			const state = docStates[doc];
			if (state && (state.status === 'idle' || state.status === 'failed')) {
				await handleProcess(doc);
				if (docStates[doc].status === 'pending_review') {
					successCount++;
				} else {
					failureCount++;
				}
			}
		}

		if (batchAbortController.signal.aborted) {
			toaster.warning({
				title: 'Batch Processing Cancelled',
				description: 'The operation was cancelled.'
			});
		} else if (successCount > 0 && failureCount === 0) {
			toaster.success({
				title: 'Batch Processing Complete',
				description: `${successCount} documents processed.`
			});
		} else if (successCount === 0 && failureCount > 0) {
			toaster.error({
				title: 'Batch Processing Failed',
				description: `${failureCount} documents failed to process.`
			});
		} else if (successCount > 0 && failureCount > 0) {
			toaster.warning({
				title: 'Batch Processing Partially Complete',
				description: `${successCount} succeeded, ${failureCount} failed.`
			});
		}

		isBatchProcessing = false;
		batchAbortController = null;
	}

	function cancelBatchProcess() {
		if (batchAbortController) {
			batchAbortController.abort();
		}
	}

	function handleAccept(filename: string) {
		const state = docStates[filename];
		state.status = 'accepted';
		state.isLocked = true;
	}

	function handleReject(filename: string) {
		const state = docStates[filename];
		state.status = 'idle';
		state.summary = undefined;
		state.error = undefined;
	}

	function toggleLock(filename: string) {
		const state = docStates[filename];
		state.isLocked = !state.isLocked;
	}
</script>

<div class="card preset-tonal space-y-4 p-4">
	<h3 class="h3">Document Preprocessing</h3>

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
									checked={selectionInfo().selectAllState.checked}
									indeterminate={selectionInfo().selectAllState.indeterminate}
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
								<td>
									<input type="checkbox" class="checkbox" bind:checked={selected[doc]} />
								</td>
								<td class="truncate" title={doc}>{doc}</td>
								<td>
									{#if state}
										<span
											class="badge {state.status === 'accepted'
												? 'preset-filled-success'
												: state.status === 'failed'
													? 'preset-filled-error'
													: 'preset-filled-surface'}"
										>
											{state.status.replace('_', ' ')}
										</span>
									{/if}
								</td>
								<td>
									{#if state?.status === 'pending_review' || state?.status === 'accepted'}
										<div class="space-y-1 text-xs">
											<p><strong>Content Type:</strong> {state.summary.content_type}</p>
											{#if state.summary?.sheet_names}
												<p><strong>Sheets:</strong> {state.summary.sheet_names.join(', ')}</p>
												{#if state.summary?.csv_files}
													<p><strong>CSV Files:</strong> {state.summary.csv_files.join(', ')}</p>
												{/if}
												<div>
													<strong>Preview:</strong>
													<pre
														class="bg-surface-300 dark:bg-surface-700 max-h-24 overflow-auto rounded p-1 font-mono">{JSON.stringify(
															state.summary.data_preview,
															null,
															2
														)}</pre>
												</div>
											{:else if state.summary?.page_count}
												<p><strong>Pages:</strong> {state.summary.page_count}</p>
												<p>
													<strong>Preview:</strong>
													<span class="bg-surface-300 dark:bg-surface-700 rounded p-1 font-mono"
														>{state.summary.text_preview}...</span
													>
												</p>
											{:else if state.summary?.text_preview}
												<p>
													<strong>Preview:</strong>
													<span class="bg-surface-300 dark:bg-surface-700 rounded p-1 font-mono"
														>{state.summary.text_preview}...</span
													>
												</p>
											{/if}
										</div>
									{:else if state?.status === 'failed'}
										<p class="text-error-500 text-xs" title={state.error}>{state.error}</p>
									{/if}
								</td>
								<td>
									{#if state}
										<div class="flex items-center gap-2">
											{#if state.status === 'idle'}
												<button
													class="btn btn-sm preset-filled"
													onclick={() => handleProcess(doc)}
													disabled={isBatchProcessing}>Process</button
												>
											{:else if state.status === 'processing'}
												<p>Processing...</p>
											{:else if state.status === 'failed'}
												<button
													class="btn btn-sm preset-filled-error"
													onclick={() => handleProcess(doc)}
													disabled={isBatchProcessing}>Retry</button
												>
											{:else if state.status === 'pending_review'}
												<button
													class="btn btn-sm preset-filled-success"
													onclick={() => handleAccept(doc)}
													disabled={state.isLocked || isBatchProcessing}
												>
													<ThumbsUp class="h-4 w-4" />
												</button>
												<button
													class="btn btn-sm preset-filled-error"
													onclick={() => handleReject(doc)}
													disabled={state.isLocked || isBatchProcessing}
												>
													<ThumbsDown class="h-4 w-4" />
												</button>
											{:else if state.status === 'accepted'}
												<div class="text-success-500 flex items-center gap-2">
													<Check class="h-5 w-5" />
													<span>Accepted</span>
												</div>
											{/if}

											{#if (state.status === 'pending_review' || state.status === 'accepted') && !isBatchProcessing}
												<button
													class="btn-icon btn-icon-sm"
													onclick={() => toggleLock(doc)}
													title={state.isLocked ? 'Unlock' : 'Lock'}
												>
													{#if state.isLocked}
														<Lock class="h-4 w-4" />
													{:else}
														<Unlock class="h-4 w-4" />
													{/if}
												</button>
											{/if}
										</div>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<div class="card preset-tonal-surface p-4">
				<h4 class="h4 mb-2">Actions</h4>
				<div class="flex gap-2">
					<button
						class="btn preset-filled"
						onclick={handleBatchProcess}
						disabled={!selectionInfo().canBatchProcess || isBatchProcessing}
					>
						{#if isBatchProcessing}
							Processing...
						{:else}
							Process Selected
						{/if}
					</button>
					{#if isBatchProcessing}
						<button class="btn preset-tonal-error" onclick={cancelBatchProcess}>
							<X class="mr-2 h-4 w-4" />
							Cancel
						</button>
					{/if}
				</div>
			</div>
		</div>
	{/if}
</div>
