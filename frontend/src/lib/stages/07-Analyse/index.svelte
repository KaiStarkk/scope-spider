<script lang="ts">
	import { runState } from '$lib/shared/stores/run.svelte';
	import { browser } from '$app/environment';
	import { toaster } from '$lib/shared/stores/toast';
	import {
		Check,
		ThumbsUp,
		ThumbsDown,
		Lock,
		Unlock,
		ChevronDown,
		ChevronUp
	} from '@lucide/svelte/icons';
	import { Accordion } from '@skeletonlabs/skeleton-svelte';
	import { onMount } from 'svelte';

	type DocStatus = {
		status: 'idle' | 'analyzing' | 'pending_review' | 'accepted' | 'failed';
		results?: any;
		error?: string;
		isLocked?: boolean;
	};

	let filteredDocs = $state<string[]>([]);
	let docStates = $state<Record<string, DocStatus>>({});
	let isLoading = $state(true);
	let selected: Record<string, boolean> = $state({});

	function getDefaultAiConfig() {
		return {
			provider: 'openai',
			apiKey: '',
			model: 'gpt-5-nano',
			temperature: 0.1,
			top_p: 1,
			top_k: 1,
			max_output_tokens: 2048,
			frequency_penalty: 0,
			presence_penalty: 0,
			system_prompt: `You are a specialized AI assistant for extracting greenhouse gas (GHG) emissions data from corporate sustainability reports.
Your task is to analyze the provided text and extract the total Scope 1, Scope 2, and Scope 3 emissions for the most recent reporting year.

**Instructions:**
1.  Read the text provided by the user.
2.  Identify the values for "Scope 1", "Scope 2", and "Scope 3" total emissions.
3.  The values are typically reported in tonnes of carbon dioxide equivalent (tCO2-e).
4.  Pay close attention to the numbers. Ensure you are extracting the total emissions, not other figures.
5.  Return the data in a clean JSON format, with the keys "Scope 1", "Scope 2", and "Scope 3". The values should be integers.
6.  If a value for a particular scope is not found, the value should be \`null\`.

**Example Output:**
\`\`\`json
{
  "Scope 1": 7313,
  "Scope 2": 49565,
  "Scope 3": 71840,
  "Carbon Credit Information": "Purchased 58 ACCU credits and 29 VCS credits."
}
\`\`\``
		};
	}

	let aiConfig = $state(getDefaultAiConfig());

	$effect(() => {
		const isComplete =
			filteredDocs.length > 0 && filteredDocs.every((doc) => docStates[doc]?.status === 'accepted');
		runState.setStepValidity(6, isComplete);
	});

	$effect(() => {
		const serialized = JSON.stringify(aiConfig);
		if (runState.state.aiConfig && JSON.stringify(runState.state.aiConfig) === serialized) {
			return;
		}
		runState.updateAiConfig(JSON.parse(serialized));
	});

	onMount(async () => {
		isLoading = true;
		if (browser) {
			const projectDirectory = runState.state.projectName;
			if (!projectDirectory) {
				isLoading = false;
				return;
			}
			try {
				const state = await runState.getState();
				if (state.aiConfig) {
					aiConfig = { ...getDefaultAiConfig(), ...state.aiConfig };
				}

				const response = await fetch(
					`/api/documents/list?project_directory=${encodeURIComponent(
						projectDirectory
					)}&folder=filtered`
				);
				if (!response.ok) throw new Error('Failed to fetch filtered documents.');
				const data = await response.json();
				filteredDocs = data.documents;

				const initialStates: Record<string, DocStatus> = {};
				for (const doc of filteredDocs) {
					initialStates[doc] = { status: 'idle', isLocked: false };
				}
				docStates = initialStates;
				selected = {};
			} catch (error) {
				const message = error instanceof Error ? error.message : 'An unknown error occurred.';
				toaster.error({ title: 'Loading Failed', description: message });
			}
		}
		isLoading = false;
	});

	async function handleAnalyze(filename: string) {
		const state = docStates[filename];
		state.status = 'analyzing';
		try {
			const projectDirectory = runState.state.projectName;
			if (!projectDirectory) throw new Error('Project directory not set.');

			const response = await fetch('/api/ai/extract', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					project_directory: projectDirectory,
					filename: filename
				})
			});

			if (!response.ok) {
				const err = await response.json();
				throw new Error(err.detail || 'AI analysis failed.');
			}

			state.results = await response.json();
			state.status = 'pending_review';
			toaster.success({
				title: 'Analysis Complete',
				description: `${filename} is ready for review.`
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : 'An unknown error occurred.';
			toaster.error({ title: 'Analysis Failed', description: message });
			state.error = message;
			state.status = 'failed';
		}
	}

	async function handleBatchAnalyze() {
		for (const doc of Object.keys(selected)) {
			if (selected[doc]) {
				await handleAnalyze(doc);
			}
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
		state.results = undefined;
		state.error = undefined;
	}

	function toggleLock(filename: string) {
		const state = docStates[filename];
		state.isLocked = !state.isLocked;
	}

	function toggleSelectAll(checked: boolean) {
		const newSelected: Record<string, boolean> = {};
		for (const doc of filteredDocs) {
			newSelected[doc] = checked;
		}
		selected = newSelected;
	}

	const allSelected = $derived(
		filteredDocs.length > 0 && filteredDocs.every((doc) => selected[doc])
	);
	const someSelected = $derived(!allSelected && filteredDocs.some((doc) => selected[doc]));
</script>

<div class="space-y-6">
	<Accordion collapsible>
		<Accordion.Item value="ai-config">
			<h3 class="h3 font-bold">
				<Accordion.ItemTrigger>
					<span>AI Configuration</span>
					<Accordion.ItemIndicator class="group">
						<ChevronUp class="hidden size-4 group-data-[state=open]:block" />
						<ChevronDown class="block size-4 group-data-[state=open]:hidden" />
					</Accordion.ItemIndicator>
				</Accordion.ItemTrigger>
			</h3>
			<Accordion.ItemContent>
				<div class="space-y-4 pt-4">
					<div class="grid grid-cols-2 gap-4">
						<label class="label">
							<span>Provider</span>
							<select class="select" bind:value={aiConfig.provider}>
								<option value="gemini">Google Gemini</option>
								<option value="openai">OpenAI</option>
							</select>
						</label>
						<label class="label">
							<span>API Key</span>
							<input class="input" type="password" bind:value={aiConfig.apiKey} />
						</label>
					</div>
					{#if aiConfig.provider === 'gemini'}
						<div class="grid grid-cols-2 gap-4">
							<label class="label">
								<span>Model</span>
								<select class="select" bind:value={aiConfig.model}>
									<option value="gemini-1.5-flash">Gemini 1.5 Flash</option>
									<option value="gemini-1.5-pro">Gemini 1.5 Pro</option>
								</select>
							</label>
							<label class="label">
								<span>Temperature</span>
								<input class="input" type="number" step="0.1" bind:value={aiConfig.temperature} />
							</label>
						</div>
					{:else}
						<div class="grid grid-cols-2 gap-4">
							<label class="label">
								<span>Model</span>
								<select class="select" bind:value={aiConfig.model}>
									<option value="gpt-5-mini">GPT-5 Mini</option>
									<option value="gpt-5-nano">GPT-5 Nano</option>
								</select>
							</label>
							<label class="label">
								<span>Temperature</span>
								<input class="input" type="number" step="0.1" bind:value={aiConfig.temperature} />
							</label>
						</div>
						<div class="grid grid-cols-2 gap-4">
							<label class="label">
								<span>Top P</span>
								<input class="input" type="number" step="0.1" bind:value={aiConfig.top_p} />
							</label>
							<label class="label">
								<span>Frequency Penalty</span>
								<input
									class="input"
									type="number"
									step="0.1"
									bind:value={aiConfig.frequency_penalty}
								/>
							</label>
						</div>
						<label class="label">
							<span>Presence Penalty</span>
							<input
								class="input"
								type="number"
								step="0.1"
								bind:value={aiConfig.presence_penalty}
							/>
						</label>
					{/if}
					<label class="label">
						<span>System Prompt</span>
						<textarea class="textarea" rows="10" bind:value={aiConfig.system_prompt}></textarea>
					</label>
				</div>
			</Accordion.ItemContent>
		</Accordion.Item>
	</Accordion>

	<div class="card preset-tonal space-y-4 p-4">
		<div class="flex items-center justify-between">
			<h3 class="h3">Filtered Documents</h3>
			<button
				class="btn preset-filled"
				onclick={handleBatchAnalyze}
				disabled={!someSelected && !allSelected}
			>
				Analyze Selected
			</button>
		</div>

		{#if isLoading}
			<p>Loading filtered documents...</p>
		{:else if filteredDocs.length === 0}
			<p>No filtered documents found. Please complete the keyword filtering step.</p>
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
							<th>Scope 1</th>
							<th>Scope 2</th>
							<th>Scope 3</th>
							<th>Carbon Credit Info</th>
							<th>Actions</th>
						</tr>
					</thead>
					<tbody>
						{#each filteredDocs as doc (doc)}
							{@const state = docStates[doc]}
							<tr>
								<td>
									<input type="checkbox" class="checkbox" bind:checked={selected[doc]} />
								</td>
								<td class="truncate" title={doc}>{doc}</td>
								<td>
									<span
										class="badge {state?.status === 'accepted'
											? 'preset-filled-success'
											: 'preset-filled-surface'}"
									>
										{state?.status || 'idle'}
									</span>
								</td>
								<td>{state?.results?.['Scope 1'] ?? ''}</td>
								<td>{state?.results?.['Scope 2'] ?? ''}</td>
								<td>{state?.results?.['Scope 3'] ?? ''}</td>
								<td>{state?.results?.['Carbon Credit Information'] ?? ''}</td>
								<td>
									{#if state?.status === 'idle'}
										<button class="btn btn-sm" onclick={() => handleAnalyze(doc)}> Analyze </button>
									{:else if state?.status === 'analyzing'}
										<span>Analyzing...</span>
									{:else if state?.status === 'failed'}
										<button
											class="btn btn-sm preset-filled-error"
											onclick={() => handleAnalyze(doc)}
										>
											Retry
										</button>
									{:else if state?.status === 'pending_review'}
										<div class="flex gap-2">
											<button
												class="btn btn-sm preset-filled-success"
												onclick={() => handleAccept(doc)}
											>
												<ThumbsUp class="h-4 w-4" />
											</button>
											<button
												class="btn btn-sm preset-filled-error"
												onclick={() => handleReject(doc)}
											>
												<ThumbsDown class="h-4 w-4" />
											</button>
										</div>
									{:else if state?.status === 'accepted'}
										<span class="text-success-500 flex items-center gap-2">
											<Check />
											Accepted
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
