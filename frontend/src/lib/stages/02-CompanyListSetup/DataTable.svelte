<script lang="ts">
	import X from '@lucide/svelte/icons/x';

	type Props = {
		data: any[][];
		headerRow?: string[];
		visibleRows?: boolean[];
		visibleColumns?: boolean[];
		onVisibilityChange: (rows: boolean[], cols: boolean[]) => void;
	};
	let {
		data,
		headerRow = $bindable(),
		visibleRows = [],
		visibleColumns = [],
		onVisibilityChange
	}: Props = $props();

	const headerIndex = $derived(visibleRows.indexOf(true));

	$effect(() => {
		if (headerIndex !== -1) {
			headerRow = data[headerIndex];
		} else {
			headerRow = [];
		}
	});

	const visibleDataRows = $derived(
		visibleRows.filter((v, i) => v && i > headerIndex).length
	);

	function hideRow(index: number) {
		const newVisibleRows = [...visibleRows];
		newVisibleRows[index] = false;
		onVisibilityChange(newVisibleRows, visibleColumns);
	}

	function hideColumn(index: number) {
		const newVisibleColumns = [...visibleColumns];
		newVisibleColumns[index] = false;
		onVisibilityChange(visibleRows, newVisibleColumns);
	}
</script>

<div class="table-wrap h-96 overflow-auto">
	<h3 class="h4">Data Preview</h3>
	<table class="table caption-bottom">
		<thead class="sticky top-0 bg-surface-100 dark:bg-surface-900">
			<tr>
				<th class="w-12">
					{#if headerIndex !== -1}
						<button onclick={() => hideRow(headerIndex)} title="Hide Header Row">
							<X />
						</button>
					{/if}
				</th>
				{#each headerRow || [] as header, i}
					{#if visibleColumns[i]}
						<th>
							<div class="flex items-center justify-between">
								{header}
								<button onclick={() => hideColumn(i)} title="Hide Column">
									<X />
								</button>
							</div>
						</th>
					{/if}
				{/each}
			</tr>
		</thead>
		<tbody class="[&>tr]:hover:preset-tonal-primary">
			{#each data as row, i}
				{#if i > headerIndex && visibleRows[i]}
					<tr>
						<td>
							<button onclick={() => hideRow(i)} title="Hide Row">
								<X />
							</button>
						</td>
						{#each row as cell, j}
							{#if visibleColumns[j]}
								<td>{cell}</td>
							{/if}
						{/each}
					</tr>
				{/if}
			{/each}
		</tbody>
		<tfoot class="sticky bottom-0 bg-surface-100 dark:bg-surface-900">
			<tr>
				<th>Visible Data Rows</th>
				<td colspan={visibleColumns.filter(Boolean).length}>{visibleDataRows}</td>
			</tr>
		</tfoot>
	</table>
</div>
