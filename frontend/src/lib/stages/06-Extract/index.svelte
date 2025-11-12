<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';
	import { browser } from '$app/environment';
	import { toaster } from '$lib/shared/stores/toast';
	import { TagsInput } from '@skeletonlabs/skeleton-svelte';
	import { Check } from '@lucide/svelte/icons';
	import { onMount } from 'svelte';

	type DocStatus = {
		status: 'idle' | 'filtering' | 'complete' | 'failed';
		error?: string;
		snippet_count?: number;
	};

	type Rule = {
		keywords: string[];
	};

	type FilterConfig = {
		rules: Record<string, Rule>;
		beforeChars: number;
		afterChars: number;
	};

	let transcribedDocs = $state<string[]>([]);
	let docStates = $state<Record<string, DocStatus>>({});
	let isLoading = $state(true);
	let selected: Record<string, boolean> = $state({});

	function getDefaultFilterConfig(): FilterConfig {
		return {
			rules: {
				'Scope 1 Emissions': { keywords: ['Scope 1', 'Scope one', 'direct GHG'] },
				'Scope 2 Emissions': {
					keywords: ['Scope 2', 'Scope two', 'indirect GHG from electricity']
				},
				'Scope 3 Emissions': { keywords: ['Scope 3', 'Scope three', 'other indirect GHG'] },
				'Carbon Credits': { keywords: ['carbon credit', 'carbon offset', 'carbon removal'] }
			},
			beforeChars: 100,
			afterChars: 500
		};
	}
	let filterConfig = $state(getDefaultFilterConfig());

	$effect(() => {
		const isComplete =
			transcribedDocs.length > 0 &&
			transcribedDocs.every((doc) => docStates[doc]?.status === 'complete');
		runState.setStepValidity(5, isComplete);
	});

	$effect(() => {
		const serialized = JSON.stringify(filterConfig);
		// Avoid triggering a save on the initial load by checking against the stored state
		if (runState.state.filterConfig && JSON.stringify(runState.state.filterConfig) === serialized) {
			return;
		}
		runState.updateFilterConfig(JSON.parse(serialized));
	});

	onMount(() => {
		async function loadData() {
			isLoading = true;
			if (browser) {
				const projectDirectory = runState.state.projectName;
				if (!projectDirectory) {
					isLoading = false;
					return;
				}
				try {
					const state = await runState.getState();
					if (state.filterConfig) {
						filterConfig = { ...getDefaultFilterConfig(), ...state.filterConfig };
					}

					const response = await fetch(
						`/api/documents/list?project_directory=${encodeURIComponent(
							projectDirectory
						)}&folder=transcribed`
					);
					if (!response.ok) throw new Error('Failed to fetch documents.');
					const data = await response.json();
					transcribedDocs = data.documents;

					const initialStates: Record<string, DocStatus> = {};
					for (const doc of transcribedDocs) {
						initialStates[doc] = { status: 'idle' };
					}
					docStates = initialStates;
					selected = {};
				} catch (error) {
					const message = error instanceof Error ? error.message : 'An unknown error occurred.';
					toaster.error({ title: 'Loading Failed', description: message });
				}
			}
			isLoading = false;
		}
		loadData();
	});

	async function handleFilter(filename: string) {
		const state = docStates[filename];
		state.status = 'filtering';
		try {
			const projectDirectory = runState.state.projectName;
			if (!projectDirectory) throw new Error('Project directory not set.');

			const response = await fetch('/api/data/filter', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					project_directory: projectDirectory,
					filename: filename,
					rules: filterConfig.rules,
					before_chars: filterConfig.beforeChars,
					after_chars: filterConfig.afterChars
				})
			});

			if (!response.ok) {
				const err = await response.json();
				throw new Error(err.detail || 'Filtering failed.');
			}
			const result = await response.json();
			state.status = 'complete';
			state.snippet_count = result.snippet_count;
			toaster.success({ title: 'Filter Complete', description: `${filename} has been filtered.` });
		} catch (error) {
			const message = error instanceof Error ? error.message : 'An unknown error occurred.';
			toaster.error({ title: 'Filtering Failed', description: message });
			state.error = message;
			state.status = 'failed';
		}
	}

	async function handleBatchFilter() {
		for (const doc of Object.keys(selected)) {
			if (selected[doc]) {
				await handleFilter(doc);
			}
		}
	}

	function restoreDefaultRules() {
		filterConfig.rules = getDefaultFilterConfig().rules;
	}

	function toggleSelectAll(checked: boolean) {
		const newSelected: Record<string, boolean> = {};
		for (const doc of transcribedDocs) {
			newSelected[doc] = checked;
		}
		selected = newSelected;
	}

	const allSelected = $derived(
		transcribedDocs.length > 0 && transcribedDocs.every((doc) => selected[doc])
	);
	const someSelected = $derived(!allSelected && transcribedDocs.some((doc) => selected[doc]));
</script>

<div class="space-y-6">
	<div class="card preset-tonal space-y-4 p-4">
		<div class="flex items-center justify-between">
			<h3 class="h3">Keyword Filtering Rules</h3>
			<button class="btn btn-sm preset-tonal" onclick={restoreDefaultRules}>Restore Defaults</button
			>
		</div>
		<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
			{#each Object.entries(filterConfig.rules) as [ruleName, ruleDetails]}
				<div class="space-y-2">
					<TagsInput
						value={ruleDetails.keywords}
						onValueChange={(detail) => {
							if (detail) ruleDetails.keywords = detail.value;
						}}
					>
						<TagsInput.Label class="font-bold">{ruleName}</TagsInput.Label>
						<TagsInput.Control>
							<TagsInput.Context>
								{#snippet children(tagsInput)}
									{#each tagsInput().value as value, index (index)}
										<TagsInput.Item {value} {index}>
											<TagsInput.ItemPreview>
												<TagsInput.ItemText>{value}</TagsInput.ItemText>
												<TagsInput.ItemDeleteTrigger />
											</TagsInput.ItemPreview>
											<TagsInput.ItemInput />
										</TagsInput.Item>
									{/each}
								{/snippet}
							</TagsInput.Context>
							<TagsInput.Input placeholder="Add a keyword..." />
						</TagsInput.Control>
					</TagsInput>
				</div>
			{/each}
		</div>
		<div class="grid grid-cols-2 gap-4">
			<label class="label">
				<span>Characters Before Keyword</span>
				<input class="input" type="number" bind:value={filterConfig.beforeChars} />
			</label>
			<label class="label">
				<span>Characters After Keyword</span>
				<input class="input" type="number" bind:value={filterConfig.afterChars} />
			</label>
		</div>
	</div>

	<div class="card preset-tonal space-y-4 p-4">
		<div class="flex items-center justify-between">
			<h3 class="h3">Transcribed Documents</h3>
			<button
				class="btn preset-filled"
				onclick={handleBatchFilter}
				disabled={!someSelected && !allSelected}
			>
				Filter Selected
			</button>
		</div>

		{#if isLoading}
			<p>Loading documents...</p>
		{:else if transcribedDocs.length === 0}
			<p>No transcribed documents found. Please complete the preprocessing step.</p>
		{:else}
			<div class="table-container">
				<table class="table-hover table">
					<thead>
						<tr>
							<th>
								<input
									type="checkbox"
									class="checkbox"
									checked={allSelected}
									indeterminate={someSelected}
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
						{#each transcribedDocs as doc (doc)}
							{@const state = docStates[doc]}
							<tr>
								<td>
									<input type="checkbox" class="checkbox" bind:checked={selected[doc]} />
								</td>
								<td class="truncate" title={doc}>{doc}</td>
								<td>
									<span
										class="badge {state?.status === 'complete'
											? 'preset-filled-success'
											: 'preset-filled-surface'}"
									>
										{state?.status || 'idle'}
									</span>
								</td>
								<td>
									{#if state?.status === 'complete'}
										{state.snippet_count} snippets found
									{/if}
								</td>
								<td>
									{#if state?.status === 'idle'}
										<button class="btn btn-sm" onclick={() => handleFilter(doc)}> Filter </button>
									{:else if state?.status === 'filtering'}
										<span>Filtering...</span>
									{:else if state?.status === 'failed'}
										<button
											class="btn btn-sm preset-filled-error"
											onclick={() => handleFilter(doc)}
										>
											Retry
										</button>
									{:else if state?.status === 'complete'}
										<span class="text-success-500 flex items-center gap-2">
											<Check />
											Complete
										</span>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</div>
</div>
