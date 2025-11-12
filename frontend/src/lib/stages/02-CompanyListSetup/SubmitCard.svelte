<script lang="ts">
	import { Check, X } from '@lucide/svelte';
	import { toaster } from '$lib/shared/stores/toast';
	import { runState } from '$lib/shared/stores/run.svelte';

	type Props = {
		mappings: Record<string, string>;
		data: any[][];
		headers: string[];
		visibleRows: boolean[];
		visibleColumns: boolean[];
		files: File[];
	};
	let { mappings, data, headers, visibleRows, visibleColumns }: Props = $props();

	const projectDirectory = $derived(runState.state.projectName);

	const requiredFields = ['company_name', 'industry_classification', 'stock_ticker'];

	const isMappingComplete = $derived(requiredFields.every((field) => mappings[field]));
	const isProjectDirSet = $derived(projectDirectory && projectDirectory.trim() !== '');
	const isReadyForSubmission = $derived(isMappingComplete && isProjectDirSet);
	let lastSubmittedMappings = $state<Record<string, string> | null>(null);

	async function handleSubmit() {
		if (!projectDirectory) {
			toaster.error({ title: 'Error', description: 'Project directory is not set.' });
			return;
		}

		const companyData = data
			.slice(1) // Skip header row
			.map((row, rowIndex) => ({ row, originalIndex: rowIndex + 1 }))
			.filter((item) => visibleRows[item.originalIndex])
			.map(({ row }) => {
				const company: Record<string, any> = {};
				for (const field of requiredFields) {
					const mappedHeader = mappings[field];
					const headerIndex = headers.indexOf(mappedHeader);
					if (headerIndex !== -1) {
						company[field] = row[headerIndex];
					}
				}
				return company;
			});

		const validCompanies = companyData.filter((company) => {
			return requiredFields.every((field) => company[field] != null && company[field] !== '');
		});

		if (validCompanies.length === 0) {
			toaster.error({
				title: 'No Data',
				description: 'No valid company rows to submit. Please check your data and mappings.'
			});
			return;
		}

		// Persist companies directly into the monolithic state
		try {
			runState.setStepValidity(1, true);
			await runState.setProcessedCompanies(validCompanies);
		} catch (error) {
			console.error('Error saving companies to state:', error);
			let errorMessage = 'An unexpected error occurred.';
			if (error instanceof Error) {
				errorMessage = error.message;
			}
			toaster.error({
				title: 'Error',
				description: `❌ ${errorMessage}`
			});
		}
	}

	$effect(() => {
		if (
			isReadyForSubmission &&
			JSON.stringify(mappings) !== JSON.stringify(lastSubmittedMappings)
		) {
			handleSubmit();
			lastSubmittedMappings = { ...mappings };
		}
	});

	// Reset submission status if the underlying data changes, allowing for re-submission.
	$effect(() => {
		const _ = data;
		const __ = mappings;
		lastSubmittedMappings = null; // Reset last submitted mappings on data change
	});
</script>

<div class="card preset-glass-surface space-y-4 p-4">
	<h3 class="h3">Submission Checklist</h3>

	<ul class="list">
		<li
			class="flex items-center"
			class:text-success-500={isMappingComplete}
			class:text-warning-500={!isMappingComplete}
		>
			{#if isMappingComplete}
				<Check class="mr-2" />
			{:else}
				<X class="mr-2" />
			{/if}
			<span>Column mappings are complete.</span>
		</li>
		<li
			class="flex items-center"
			class:text-success-500={isProjectDirSet}
			class:text-warning-500={!isProjectDirSet}
		>
			{#if isProjectDirSet}
				<Check class="mr-2" />
			{:else}
				<X class="mr-2" />
			{/if}
			<span>Project directory is set.</span>
		</li>
	</ul>

	{#if isReadyForSubmission}
		<div class="alert preset-tonal-success">
			<p>Company data is valid and has been submitted.</p>
		</div>
	{/if}
</div>
