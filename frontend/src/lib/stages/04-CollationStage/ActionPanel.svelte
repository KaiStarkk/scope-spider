<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	let {
		canBatchSearch,
		canBatchDownload,
		isBatchProcessing,
		batchOperation
	}: {
		canBatchSearch: boolean;
		canBatchDownload: boolean;
		isBatchProcessing: boolean;
		batchOperation: 'search' | 'download' | null;
	} = $props();

	const dispatch = createEventDispatcher();
</script>

<div class="card variant-soft p-4">
	<h4 class="h4 mb-4">Actions</h4>
	<div class="space-y-2">
		<button
			class="btn variant-filled w-full"
			disabled={!canBatchSearch || isBatchProcessing}
			onclick={() => dispatch('batchSearch')}
		>
			{#if batchOperation === 'search'}
				Searching...
			{:else}
				Search Selected
			{/if}
		</button>
		<button
			class="btn variant-filled w-full"
			disabled={!canBatchDownload || isBatchProcessing}
			onclick={() => dispatch('batchDownload')}
		>
			{#if batchOperation === 'download'}
				Downloading...
			{:else}
				Download Selected
			{/if}
		</button>
	</div>
</div>
