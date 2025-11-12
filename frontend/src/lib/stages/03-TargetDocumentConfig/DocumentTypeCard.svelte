<script lang="ts">
	import type { DocumentType } from './types';
	import StyledTagsInput from './StyledTagsInput.svelte';
	import X from '@lucide/svelte/icons/x';

	let { docType = $bindable(), onRemove }: { docType: DocumentType; onRemove: () => void } =
		$props();
</script>

<div class="card preset-tonal flex flex-col space-y-4 p-4">
	<div class="flex items-start justify-between">
		<h4 class="h4 flex-grow pr-2">{docType.name}</h4>
		<button
			type="button"
			class="btn-icon btn-icon-sm"
			title="Remove Document Type"
			onclick={onRemove}
		>
			<X class="h-4 w-4" />
		</button>
	</div>
	<div class="space-y-2">
		<p class="text-sm font-medium">File Type</p>
		<div class="flex items-center gap-4">
			<label class="flex items-center gap-2 text-sm">
				<input type="radio" class="radio" bind:group={docType.fileType} value="pdf" />
				PDF
			</label>
			<label class="flex items-center gap-2 text-sm">
				<input type="radio" class="radio" bind:group={docType.fileType} value="xlsx" />
				XLSX
			</label>
			<label class="flex items-center gap-2 text-sm">
				<input type="radio" class="radio" bind:group={docType.fileType} value="either" />
				Either
			</label>
		</div>
	</div>
	<div class="flex-grow">
		<StyledTagsInput
			bind:value={docType.terms}
			label="Document-specific search terms"
			placeholder="Add specific term"
			name="doc-terms-{docType.id.toString()}"
		/>
	</div>
</div>
