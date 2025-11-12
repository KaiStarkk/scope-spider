<script lang="ts">
	import DataMapping from './DataMapping.svelte';
	import DataTable from './DataTable.svelte';
	import SubmitCard from './SubmitCard.svelte';
	import type { CompanyData } from '$lib/shared/stores/run.svelte';
	import { runState } from '$lib/shared/stores/run.svelte';

	type Props = {
		companyData: CompanyData;
		headerRow: string[];
		onVisibilityChange: (rows: boolean[], cols: boolean[]) => void;
	};
	let {
		companyData,
		headerRow = $bindable(),
		onVisibilityChange
	}: Props = $props();

	// Create a local bindable mappings proxy to satisfy Svelte ownership rules
	let mappings = $state<Record<string, string>>({ ...companyData.mappings });
	let prevSerialized = '';
	$effect(() => {
		const currentSerialized = JSON.stringify(mappings);
		if (currentSerialized !== prevSerialized) {
			prevSerialized = currentSerialized;
			runState.updateMappings(mappings);
		}
	});
</script>

<div class="space-y-4">
	<DataMapping headers={headerRow} bind:mappings visibleColumns={companyData.visibleColumns} />
	<DataTable
		data={companyData.data}
		bind:headerRow
		visibleRows={companyData.visibleRows}
		visibleColumns={companyData.visibleColumns}
		{onVisibilityChange}
	/>
	<SubmitCard
		mappings={companyData.mappings}
		data={companyData.data}
		headers={headerRow}
		visibleRows={companyData.visibleRows}
		visibleColumns={companyData.visibleColumns}
		files={companyData.fileName ? [new File([], companyData.fileName)] : []}
	/>
</div>
