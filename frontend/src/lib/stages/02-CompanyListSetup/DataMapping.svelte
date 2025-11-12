<script lang="ts">
	import { Listbox, useListCollection } from '@skeletonlabs/skeleton-svelte';

	type Props = {
		headers: string[];
		mappings?: Record<string, string>;
		visibleColumns: boolean[];
	};
	let { headers, mappings = $bindable(), visibleColumns }: Props = $props();

	if (!mappings) {
		mappings = {};
	}

	const visibleHeaders = $derived(headers.filter((_, i) => visibleColumns[i]));

	const collection = $derived(
		useListCollection({
			items: visibleHeaders.map((h) => ({ label: h, value: h })),
			itemToString: (item) => item.label,
			itemToValue: (item) => item.value
		})
	);

	let companyNameSelection = $state(mappings.company_name ? [mappings.company_name] : []);
	let industrySelection = $state(
		mappings.industry_classification ? [mappings.industry_classification] : []
	);
	let tickerSelection = $state(mappings.stock_ticker ? [mappings.stock_ticker] : []);

	$effect(() => {
		if (mappings) {
			mappings.company_name = companyNameSelection[0] ?? '';
			mappings.industry_classification = industrySelection[0] ?? '';
			mappings.stock_ticker = tickerSelection[0] ?? '';
		}
	});
</script>

<div class="space-y-4">
	<h3 class="h4">Map Columns</h3>
	<p>Select the columns from your file that correspond to the required fields.</p>
	<div class="grid grid-cols-3 gap-4">
		<!-- Company Name -->
		<label class="label">
			<span class="capitalize">Company Name</span>
			<Listbox
				class="w-full"
				{collection}
				value={companyNameSelection}
				onValueChange={(e) => (companyNameSelection = e.value)}
			>
				<Listbox.Label class="label w-full text-left">
					{companyNameSelection[0] ?? 'Select a column'}
				</Listbox.Label>
				<Listbox.Content class="max-h-64 overflow-y-auto">
					{#each collection.items as item (item.value)}
						<Listbox.Item {item}>
							<Listbox.ItemText>{item.label}</Listbox.ItemText>
							<Listbox.ItemIndicator />
						</Listbox.Item>
					{/each}
				</Listbox.Content>
			</Listbox>
		</label>

		<!-- Industry Classification -->
		<label class="label">
			<span class="capitalize">Industry Classification</span>
			<Listbox
				class="w-full"
				{collection}
				value={industrySelection}
				onValueChange={(e) => (industrySelection = e.value)}
			>
				<Listbox.Label class="label w-full text-left">
					{industrySelection[0] ?? 'Select a column'}
				</Listbox.Label>
				<Listbox.Content class="max-h-64 overflow-y-auto">
					{#each collection.items as item (item.value)}
						<Listbox.Item {item}>
							<Listbox.ItemText>{item.label}</Listbox.ItemText>
							<Listbox.ItemIndicator />
						</Listbox.Item>
					{/each}
				</Listbox.Content>
			</Listbox>
		</label>

		<!-- Stock Ticker -->
		<label class="label">
			<span class="capitalize">Stock Ticker</span>
			<Listbox
				class="w-full"
				{collection}
				value={tickerSelection}
				onValueChange={(e) => (tickerSelection = e.value)}
			>
				<Listbox.Label class="label w-full text-left">
					{tickerSelection[0] ?? 'Select a column'}
				</Listbox.Label>
				<Listbox.Content class="max-h-64 overflow-y-auto">
					{#each collection.items as item (item.value)}
						<Listbox.Item {item}>
							<Listbox.ItemText>{item.label}</Listbox.ItemText>
							<Listbox.ItemIndicator />
						</Listbox.Item>
					{/each}
				</Listbox.Content>
			</Listbox>
		</label>
	</div>
</div>
